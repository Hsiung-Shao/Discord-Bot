"""表情回應領取身分組 (Reaction Role)。

管理員用 /rr 指令在頻道貼一則面板訊息,綁定「表情符號 → 身分組」,
成員點該表情就自動取得身分組,移除表情就收回。

設定按 guild_id 分層存在 data/reaction_roles.json,每個伺服器各自一份
emoji → 身分組對照,互不干擾;同一個 bot 在 A 伺服器發的 emoji 不會影響 B。

用 raw reaction 事件(on_raw_reaction_add/remove)而非快取事件,因此不受
message cache 限制 — bot 重啟後不需要重新註冊任何東西,舊面板照常運作。
"""

import os
import json
import re
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from config import REACTION_ROLES_FILE
import logging
from utils.logger import get_logger

logger = get_logger("ReactionRoles", level=logging.WARNING)

DEFAULT_TITLE = "🎭 領取身分組"
DEFAULT_DESCRIPTION = "點下方的表情符號即可領取對應身分組,再點一次(移除回應)就會收回。"
DEFAULT_COLOR = 0x5865F2

# Discord 單一訊息最多 20 種不同的表情回應
MAX_MAPPINGS = 20

# /rr sync 單次最多補發的人次,避免一次打爆 API
MAX_SYNC_GRANTS = 500

# 面板 embed 只是展示,絕不真的 tag 到人
NO_MENTIONS = discord.AllowedMentions.none()

# <:name:123> / <a:name:123>
CUSTOM_EMOJI_RE = re.compile(r"<(a?):([A-Za-z0-9_~]{2,32}):(\d+)>")

# 可放面板的頻道型別(專案大量使用討論串,不能只收 TextChannel)
PanelChannel = Union[discord.TextChannel, discord.Thread]


class PanelError(Exception):
    """面板操作失敗,訊息會直接回給指令操作者。"""


def _parse_color(raw: Optional[str], default: int = DEFAULT_COLOR) -> int:
    """解析 #5865F2 / 0x5865F2 / 5865F2 三種寫法,失敗回預設色。"""
    if not raw:
        return default
    s = raw.strip().lstrip("#")
    if s.lower().startswith("0x"):
        s = s[2:]
    try:
        value = int(s, 16)
    except ValueError:
        return default
    return value if 0 <= value <= 0xFFFFFF else default


def _emoji_key(emoji) -> str:
    """把 PartialEmoji / Emoji / 純字串正規化成比對用的鍵。

    自訂 emoji 用 **id**(名稱隨時可被伺服器管理員改掉,id 不會變),
    Unicode emoji 用字元本身。事件端與設定端共用這一支,兩邊才對得上。
    """
    emoji_id = getattr(emoji, "id", None)
    if emoji_id:
        return str(emoji_id)
    name = getattr(emoji, "name", None)
    return (name or str(emoji)).strip()


def _parse_emoji_input(raw: str) -> Optional[discord.PartialEmoji]:
    """把使用者在指令裡打的 emoji 轉成 PartialEmoji。

    Unicode emoji 不做嚴格驗證 — 是否真的可用交給 message.add_reaction 判斷,
    那才是唯一可靠的檢查(打錯會回 HTTP 400 Unknown Emoji)。
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    m = CUSTOM_EMOJI_RE.fullmatch(raw)
    if m:
        return discord.PartialEmoji(
            animated=bool(m.group(1)), name=m.group(2), id=int(m.group(3))
        )
    if len(raw) > 32:  # 明顯不是 emoji,擋掉避免送出無意義的 API 請求
        return None
    return discord.PartialEmoji(name=raw)


def _parse_message_id(raw: str) -> Optional[int]:
    """接受純 ID,也接受直接貼訊息連結(…/channels/guild/channel/message)。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "/" in raw:
        raw = raw.rstrip("/").split("/")[-1]
    return int(raw) if raw.isdigit() else None


class ReactionRoles(commands.Cog):
    # Group 必須是 class attribute,discord.py 才會把底下的子指令綁到這個 cog
    rr_group = app_commands.Group(
        name="rr",
        description="表情回應領取身分組管理",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = self._load_config()
        # 已私訊過「領取失敗」的 (guild_id, user_id, role_id),避免權限一直錯就一直洗 DM
        self._dm_notified: set[tuple[int, int, int]] = set()

    # ────────────────────────── 設定檔存取 ──────────────────────────

    @staticmethod
    def _default_config() -> dict:
        return {"guilds": {}}

    def _load_config(self) -> dict:
        if not os.path.exists(REACTION_ROLES_FILE):
            config = self._default_config()
            self._save_config(config)
            return config
        try:
            with open(REACTION_ROLES_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"載入領取身分組設定失敗,改用空設定: {e}")
            return self._default_config()

        if not isinstance(config, dict):
            logger.error("領取身分組設定格式非預期(需為物件),改用空設定")
            return self._default_config()
        config.setdefault("guilds", {})
        return config

    def _save_config(self, config: Optional[dict] = None):
        if config is None:
            config = self.config
        directory = os.path.dirname(REACTION_ROLES_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(REACTION_ROLES_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

    def _panels(self, guild_id: int, create: bool = False) -> dict:
        """取得某伺服器的面板 dict(key 為 message_id 字串)。"""
        guilds = self.config.setdefault("guilds", {})
        key = str(guild_id)
        if key not in guilds:
            if not create:
                return {}
            guilds[key] = {"panels": {}}
        return guilds[key].setdefault("panels", {})

    def _get_panel(self, guild_id: int, message_id: int) -> Optional[dict]:
        return self._panels(guild_id).get(str(message_id))

    @staticmethod
    def _find_mapping(panel: dict, emoji_key: str) -> Optional[dict]:
        for mapping in panel.get("mappings", []):
            if mapping.get("emoji_key") == emoji_key:
                return mapping
        return None

    # ────────────────────────── 事件:領取 / 收回 ──────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, adding=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, adding=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, adding: bool):
        """便宜的過濾放前面 — 伺服器裡絕大多數 reaction 都會在前三關就 return。"""
        if payload.guild_id is None:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        panel = self._get_panel(payload.guild_id, payload.message_id)
        if panel is None:
            return

        mapping = self._find_mapping(panel, _emoji_key(payload.emoji))
        if mapping is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        role = guild.get_role(mapping.get("role_id", 0))
        if role is None:
            logger.warning(
                f"[{guild.id}] 面板 {payload.message_id} 綁定的身分組 "
                f"{mapping.get('role_id')} 已不存在,請用 /rr remove 清掉"
            )
            return

        member = payload.member  # 只有 add 事件帶得到,remove 一定是 None
        if member is None:
            member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.NotFound:
                return
            except discord.HTTPException as e:
                logger.warning(f"[{guild.id}] 取得成員 {payload.user_id} 失敗: {e}")
                return
        if member.bot:
            return

        action = "領取" if adding else "取消"
        try:
            if adding:
                if role in member.roles:
                    return
                await member.add_roles(role, reason=f"Reaction Role 面板 {payload.message_id}")
            else:
                if role not in member.roles:
                    return
                await member.remove_roles(role, reason=f"Reaction Role 面板 {payload.message_id}")
            logger.info(f"[{guild.id}] {member} {action} 身分組「{role.name}」")
        except discord.Forbidden:
            logger.error(
                f"[{guild.id}] 無權限為 {member} {action}身分組「{role.name}」— "
                "請確認 bot 有「管理身分組」權限,且 bot 的身分組位階高於該身分組"
            )
            if adding:
                await self._notify_failure(guild, member, role)
        except discord.HTTPException as e:
            logger.error(f"[{guild.id}] 為 {member} {action}身分組「{role.name}」失敗: {e}")

    async def _notify_failure(self, guild: discord.Guild, member: discord.Member, role: discord.Role):
        """權限不足時私訊通知成員。DM 被關掉就只留 log,不在頻道洗版。"""
        token = (guild.id, member.id, role.id)
        if token in self._dm_notified:
            return
        self._dm_notified.add(token)
        if len(self._dm_notified) > 1000:
            self._dm_notified.clear()
        try:
            await member.send(
                f"⚠️ 在 **{guild.name}** 領取身分組「{role.name}」失敗,"
                "機器人權限不足。請聯繫伺服器管理員。"
            )
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """面板訊息被刪掉就順手清掉設定,不留孤兒資料。"""
        if payload.guild_id is None:
            return
        panels = self._panels(payload.guild_id)
        if panels.pop(str(payload.message_id), None) is not None:
            self._save_config()
            logger.info(f"[{payload.guild_id}] 面板訊息 {payload.message_id} 已被刪除,設定同步移除")

    # ────────────────────────── 面板訊息操作 ──────────────────────────

    def _build_panel_embed(self, guild: discord.Guild, panel: dict) -> discord.Embed:
        embed = discord.Embed(
            title=panel.get("title") or DEFAULT_TITLE,
            description=panel.get("description") or DEFAULT_DESCRIPTION,
            color=panel.get("color", DEFAULT_COLOR),
        )
        lines = []
        for mapping in panel.get("mappings", []):
            role = guild.get_role(mapping.get("role_id", 0))
            role_text = role.mention if role else f"`(身分組 {mapping.get('role_id')} 已被刪除)`"
            lines.append(f"{mapping.get('emoji_raw', '?')} 　{role_text}")
        embed.add_field(
            name="可領取的身分組",
            value="\n".join(lines) or "(管理員尚未綁定任何身分組)",
            inline=False,
        )
        embed.set_footer(text="點表情符號領取,移除回應即可取消")
        return embed

    async def _fetch_panel_message(
        self, guild: discord.Guild, panel: dict, message_id: int
    ) -> discord.Message:
        channel_id = panel.get("channel_id")
        channel = guild.get_channel_or_thread(channel_id) if channel_id else None
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, ValueError, TypeError):
                raise PanelError(f"找不到面板所在頻道(ID {channel_id}),或 bot 無權存取")
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            raise PanelError("面板訊息已不存在(可能被刪除),請用 `/rr delete` 清掉設定後重建")
        except discord.Forbidden:
            raise PanelError("bot 沒有讀取該訊息的權限")

    async def _refresh_panel_message(
        self, guild: discord.Guild, panel: dict, message: discord.Message
    ):
        """把最新的對照表寫回面板 embed。納管的他人訊息編輯不了,直接跳過。"""
        if not panel.get("editable", True):
            return
        try:
            await message.edit(embed=self._build_panel_embed(guild, panel), allowed_mentions=NO_MENTIONS)
        except discord.HTTPException as e:
            logger.warning(f"[{guild.id}] 更新面板 {message.id} 顯示內容失敗: {e}")

    def _check_role_assignable(self, guild: discord.Guild, role: discord.Role) -> Optional[str]:
        """回傳錯誤訊息;None 代表這個身分組可以發。把失敗擋在設定階段。"""
        me = guild.me
        if me is None:
            return "無法取得 bot 在此伺服器的成員資料"
        if not me.guild_permissions.manage_roles:
            return "bot 缺少「管理身分組 (Manage Roles)」權限,請先到伺服器設定給權限"
        if role.is_default():
            return "不能發放 @everyone"
        if role.managed:
            return f"「{role.name}」是機器人或整合服務自動管理的身分組,無法手動發放"
        if role >= me.top_role:
            return (
                f"「{role.name}」的位階高於或等於 bot 的身分組「{me.top_role.name}」,"
                "Discord 不允許發放。請到伺服器設定把 bot 的身分組拖到它上面"
            )
        return None

    # ────────────────────────── 指令輔助 ──────────────────────────

    async def _panel_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        current = (current or "").lower()
        choices = []
        for message_id, panel in self._panels(interaction.guild_id).items():
            label = f"{panel.get('title') or '(無標題)'} — {message_id}"
            if current and current not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label[:100], value=message_id))
        return choices[:25]

    def _resolve_panel(self, guild_id: int, message_id: str) -> tuple[int, dict]:
        parsed = _parse_message_id(message_id)
        if parsed is None:
            raise PanelError(f"`{message_id}` 不是合法的訊息 ID 或訊息連結")
        panel = self._get_panel(guild_id, parsed)
        if panel is None:
            raise PanelError(
                f"這個伺服器沒有 ID 為 `{parsed}` 的面板,用 `/rr list` 看看有哪些"
            )
        return parsed, panel

    @staticmethod
    def _jump_url(guild_id: int, panel: dict, message_id: str) -> str:
        return f"https://discord.com/channels/{guild_id}/{panel.get('channel_id')}/{message_id}"

    # ────────────────────────── Slash 指令 ──────────────────────────

    @rr_group.command(name="create", description="在指定頻道發一則領取身分組的面板訊息")
    @app_commands.describe(
        channel="面板要發到哪個頻道",
        title="面板標題(選填)",
        description="面板說明文字(選填,用 \\n 換行)",
        color="Embed 顏色,例如 #5865F2(選填)",
    )
    async def rr_create(
        self,
        interaction: discord.Interaction,
        channel: PanelChannel,
        title: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 這個指令只能在伺服器內使用。", ephemeral=True)
            return

        panel = {
            "channel_id": channel.id,
            "title": (title or DEFAULT_TITLE).strip(),
            "description": (description or DEFAULT_DESCRIPTION).replace("\\n", "\n").strip(),
            "color": _parse_color(color),
            "editable": True,
            "mappings": [],
        }

        try:
            message = await channel.send(
                embed=self._build_panel_embed(guild, panel), allowed_mentions=NO_MENTIONS
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ bot 沒有在 {channel.mention} 發言的權限。", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 發送面板失敗:{e}", ephemeral=True)
            return

        self._panels(guild.id, create=True)[str(message.id)] = panel
        self._save_config()
        logger.info(f"[{guild.id}] {interaction.user} 在 #{channel} 建立面板 {message.id}")

        await interaction.followup.send(
            f"✅ 面板已建立:{message.jump_url}\n"
            f"訊息 ID:`{message.id}`\n"
            f"接著用 `/rr add message_id:{message.id} emoji: role:` 綁定身分組。",
            ephemeral=True,
        )

    @rr_group.command(name="add", description="綁定「表情符號 → 身分組」到指定面板")
    @app_commands.describe(
        message_id="面板訊息 ID(有自動補完,也可直接貼訊息連結)",
        emoji="表情符號,例如 🔔 或 <:name:123456>(自訂 emoji 需是 bot 看得到的)",
        role="點這個表情要獲得的身分組",
    )
    @app_commands.autocomplete(message_id=_panel_autocomplete)
    async def rr_add(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
        role: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 這個指令只能在伺服器內使用。", ephemeral=True)
            return

        try:
            parsed_id, panel = self._resolve_panel(guild.id, message_id)
        except PanelError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        error = self._check_role_assignable(guild, role)
        if error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return

        partial = _parse_emoji_input(emoji)
        if partial is None:
            await interaction.followup.send(
                f"❌ `{emoji}` 看起來不是表情符號。請直接貼一個 emoji,"
                "自訂 emoji 請用 `<:名稱:ID>` 格式。",
                ephemeral=True,
            )
            return

        mappings = panel.setdefault("mappings", [])
        emoji_key = _emoji_key(partial)
        if self._find_mapping(panel, emoji_key):
            await interaction.followup.send(
                f"❌ {partial} 已經綁在這個面板了,要換身分組請先 `/rr remove`。", ephemeral=True
            )
            return
        if any(m.get("role_id") == role.id for m in mappings):
            await interaction.followup.send(
                f"❌ {role.mention} 已經綁在這個面板的其他表情上了。", ephemeral=True
            )
            return
        if len(mappings) >= MAX_MAPPINGS:
            await interaction.followup.send(
                f"❌ 單一面板最多 {MAX_MAPPINGS} 個表情(Discord 限制),請另外建一個面板。",
                ephemeral=True,
            )
            return

        try:
            message = await self._fetch_panel_message(guild, panel, parsed_id)
        except PanelError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        # 加得上 reaction 才算數 — 這是驗證 emoji 可用性唯一可靠的方式
        try:
            await message.add_reaction(partial)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ bot 沒有「新增反應 (Add Reactions)」權限,無法在面板上加表情。", ephemeral=True
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                f"❌ 無法使用 {emoji} 這個表情。自訂 emoji 必須來自 bot 也在的伺服器,"
                "Unicode emoji 請確認沒打錯。",
                ephemeral=True,
            )
            return

        mappings.append({
            "emoji_key": emoji_key,
            "emoji_raw": str(partial),
            "role_id": role.id,
        })
        self._save_config()
        await self._refresh_panel_message(guild, panel, message)
        logger.info(f"[{guild.id}] {interaction.user} 在面板 {parsed_id} 綁定 {partial} → {role.name}")

        await interaction.followup.send(
            f"✅ 已綁定 {partial} → {role.mention}(面板 `{parsed_id}`)", ephemeral=True
        )

    @rr_group.command(name="remove", description="解除面板上某個表情符號的身分組綁定")
    @app_commands.describe(
        message_id="面板訊息 ID(有自動補完)",
        emoji="要解除綁定的表情符號",
    )
    @app_commands.autocomplete(message_id=_panel_autocomplete)
    async def rr_remove(self, interaction: discord.Interaction, message_id: str, emoji: str):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 這個指令只能在伺服器內使用。", ephemeral=True)
            return

        try:
            parsed_id, panel = self._resolve_panel(guild.id, message_id)
        except PanelError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        partial = _parse_emoji_input(emoji)
        if partial is None:
            await interaction.followup.send(f"❌ `{emoji}` 不是合法的表情符號。", ephemeral=True)
            return

        mapping = self._find_mapping(panel, _emoji_key(partial))
        if mapping is None:
            await interaction.followup.send(
                f"ℹ️ {partial} 沒有綁在面板 `{parsed_id}` 上。", ephemeral=True
            )
            return

        panel["mappings"] = [m for m in panel.get("mappings", []) if m is not mapping]
        self._save_config()

        # 設定已經改掉(點了也不會再給身分組),清 reaction 失敗不影響正確性
        note = ""
        try:
            message = await self._fetch_panel_message(guild, panel, parsed_id)
            await self._refresh_panel_message(guild, panel, message)
            try:
                await message.clear_reaction(partial)
            except discord.Forbidden:
                note = "\n⚠️ bot 沒有「管理訊息」權限,面板上的舊表情要請你手動清掉。"
            except discord.HTTPException:
                note = "\n⚠️ 面板上的舊表情清除失敗,請手動移除。"
        except PanelError as e:
            note = f"\n⚠️ {e}"

        role = guild.get_role(mapping.get("role_id", 0))
        role_text = role.mention if role else f"`{mapping.get('role_id')}`"
        logger.info(f"[{guild.id}] {interaction.user} 解除面板 {parsed_id} 的 {partial} 綁定")
        await interaction.followup.send(
            f"✅ 已解除 {partial} → {role_text} 的綁定。"
            "\nℹ️ 已經領走的身分組不會自動收回。" + note,
            ephemeral=True,
        )

    @rr_group.command(name="list", description="列出本伺服器所有領取面板與其身分組對照")
    async def rr_list(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ 這個指令只能在伺服器內使用。", ephemeral=True)
            return

        panels = self._panels(guild.id)
        if not panels:
            await interaction.response.send_message(
                "⚠️ 這個伺服器還沒有任何面板,用 `/rr create` 建立第一個。", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🎭 {guild.name} 的領取面板",
            description=f"共 {len(panels)} 個面板",
            color=DEFAULT_COLOR,
        )
        for message_id, panel in list(panels.items())[:25]:
            lines = []
            for mapping in panel.get("mappings", []):
                role = guild.get_role(mapping.get("role_id", 0))
                role_text = role.mention if role else f"`(已刪除 {mapping.get('role_id')})`"
                lines.append(f"{mapping.get('emoji_raw', '?')} → {role_text}")
            body = "\n".join(lines) or "(尚未綁定任何身分組)"
            embed.add_field(
                name=f"{panel.get('title') or '(無標題)'} — `{message_id}`",
                value=(
                    f"頻道:<#{panel.get('channel_id')}>　"
                    f"[跳至訊息]({self._jump_url(guild.id, panel, message_id)})\n{body}"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @rr_group.command(name="delete", description="移除面板設定(可一併刪掉面板訊息)")
    @app_commands.describe(
        message_id="面板訊息 ID(有自動補完)",
        delete_message="是否連面板訊息一起刪掉(預設否,只移除設定)",
    )
    @app_commands.autocomplete(message_id=_panel_autocomplete)
    async def rr_delete(
        self, interaction: discord.Interaction, message_id: str, delete_message: bool = False
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 這個指令只能在伺服器內使用。", ephemeral=True)
            return

        try:
            parsed_id, panel = self._resolve_panel(guild.id, message_id)
        except PanelError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        note = ""
        if delete_message:
            try:
                message = await self._fetch_panel_message(guild, panel, parsed_id)
                await message.delete()
            except PanelError as e:
                note = f"\n⚠️ {e}"
            except discord.Forbidden:
                note = "\n⚠️ bot 沒有刪除該訊息的權限,請手動刪除。"
            except discord.HTTPException as e:
                note = f"\n⚠️ 刪除訊息失敗:{e}"

        self._panels(guild.id).pop(str(parsed_id), None)
        self._save_config()
        logger.info(f"[{guild.id}] {interaction.user} 移除面板 {parsed_id}(刪訊息={delete_message})")

        await interaction.followup.send(
            f"🗑️ 已移除面板 `{parsed_id}` 的設定。"
            "\nℹ️ 成員已領走的身分組不會被收回。" + note,
            ephemeral=True,
        )

    @rr_group.command(name="bind", description="把一則既有訊息納管成領取面板")
    @app_commands.describe(
        channel="該訊息所在的頻道",
        message_id="訊息 ID 或訊息連結",
    )
    async def rr_bind(
        self, interaction: discord.Interaction, channel: PanelChannel, message_id: str
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 這個指令只能在伺服器內使用。", ephemeral=True)
            return

        parsed_id = _parse_message_id(message_id)
        if parsed_id is None:
            await interaction.followup.send(
                f"❌ `{message_id}` 不是合法的訊息 ID 或訊息連結。", ephemeral=True
            )
            return
        if self._get_panel(guild.id, parsed_id) is not None:
            await interaction.followup.send(
                f"ℹ️ 訊息 `{parsed_id}` 已經是領取面板了,直接用 `/rr add` 綁定即可。", ephemeral=True
            )
            return

        try:
            message = await channel.fetch_message(parsed_id)
        except discord.NotFound:
            await interaction.followup.send(
                f"❌ 在 {channel.mention} 找不到訊息 `{parsed_id}`。", ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ bot 沒有讀取 {channel.mention} 訊息的權限。", ephemeral=True
            )
            return

        editable = bool(self.bot.user and message.author.id == self.bot.user.id)
        self._panels(guild.id, create=True)[str(parsed_id)] = {
            "channel_id": channel.id,
            "title": DEFAULT_TITLE,
            "description": DEFAULT_DESCRIPTION,
            "color": DEFAULT_COLOR,
            "editable": editable,
            "mappings": [],
        }
        self._save_config()
        logger.info(f"[{guild.id}] {interaction.user} 納管訊息 {parsed_id} 為面板(可編輯={editable})")

        hint = (
            "面板內容會自動更新對照表。"
            if editable
            else "⚠️ 這則訊息不是 bot 發的,內容無法自動更新 — 表情仍然有效,但對照表要你自己寫在訊息裡。"
        )
        await interaction.followup.send(
            f"✅ 已納管 {message.jump_url}\n{hint}\n"
            f"接著用 `/rr add message_id:{parsed_id} emoji: role:` 綁定身分組。",
            ephemeral=True,
        )

    @rr_group.command(name="sync", description="補發 bot 離線期間漏掉的身分組(掃描面板現有的表情回應)")
    @app_commands.describe(message_id="面板訊息 ID(有自動補完)")
    @app_commands.autocomplete(message_id=_panel_autocomplete)
    async def rr_sync(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 這個指令只能在伺服器內使用。", ephemeral=True)
            return

        try:
            parsed_id, panel = self._resolve_panel(guild.id, message_id)
            message = await self._fetch_panel_message(guild, panel, parsed_id)
        except PanelError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        # 面板上被手動清掉的表情先補回來,否則成員根本沒得點
        existing = {_emoji_key(r.emoji) for r in message.reactions}
        restored = 0
        for mapping in panel.get("mappings", []):
            if mapping.get("emoji_key") in existing:
                continue
            partial = _parse_emoji_input(mapping.get("emoji_raw", ""))
            if partial is None:
                continue
            try:
                await message.add_reaction(partial)
                restored += 1
            except discord.HTTPException as e:
                logger.warning(f"[{guild.id}] 面板 {parsed_id} 補回表情 {partial} 失敗: {e}")

        granted = 0
        skipped_roles = []
        hit_limit = False
        for reaction in message.reactions:
            mapping = self._find_mapping(panel, _emoji_key(reaction.emoji))
            if mapping is None:
                continue
            role = guild.get_role(mapping.get("role_id", 0))
            if role is None:
                skipped_roles.append(str(mapping.get("role_id")))
                continue
            error = self._check_role_assignable(guild, role)
            if error:
                skipped_roles.append(role.name)
                continue

            async for user in reaction.users():
                if user.bot:
                    continue
                member = guild.get_member(user.id)
                if member is None:
                    try:
                        member = await guild.fetch_member(user.id)
                    except discord.HTTPException:
                        continue
                if role in member.roles:
                    continue
                try:
                    await member.add_roles(role, reason=f"Reaction Role 同步 面板 {parsed_id}")
                    granted += 1
                except discord.HTTPException as e:
                    logger.warning(f"[{guild.id}] 同步時給 {member} 身分組「{role.name}」失敗: {e}")
                if granted >= MAX_SYNC_GRANTS:
                    hit_limit = True
                    break
            if hit_limit:
                break

        logger.info(
            f"[{guild.id}] {interaction.user} 同步面板 {parsed_id},"
            f"補發 {granted} 人次、補回 {restored} 個表情"
        )
        result = f"✅ 面板 `{parsed_id}` 同步完成,補發 {granted} 人次身分組。"
        if restored:
            result += f"\nℹ️ 順便補回了 {restored} 個被清掉的表情。"
        if skipped_roles:
            result += f"\n⚠️ 已跳過無法發放的身分組:{', '.join(skipped_roles)}"
        if hit_limit:
            result += f"\n⚠️ 本次已達 {MAX_SYNC_GRANTS} 人次上限,請再執行一次補完剩下的。"
        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
