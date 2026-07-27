"""NLLB-200 翻譯 Cog。

三種觸發(來源語言皆自動偵測,使用者只決定目標語言):
1. 右鍵訊息選單「翻譯」→ 彈出目標語言下拉 → 翻譯該訊息
2. 對訊息加旗幟 emoji(🇹🇼 🇯🇵 🇬🇧 …)→ 翻成該國語言並回覆
3. 指定頻道自動翻譯(owner 用管理指令設定/取消)

模型延遲載入、GPU 推論序列化等都在 core.nllb_engine 處理,本檔只負責 Discord 互動。
"""

import json
import os

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    NLLB_MODEL_NAME,
    NLLB_DEVICE,
    TRANSLATE_DEFAULT_TARGET,
    TRANSLATE_CHANNELS_FILE,
)
from core.nllb_engine import (
    NLLBEngine,
    detect_flores,
    COMMON_LANGS,
    FLORES_TO_NAME,
    FLAG_TO_FLORES,
)
from utils.logger import get_logger

logger = get_logger(__name__, channel="translate")

EMBED_COLOR = 0x3498DB


class TargetLangSelect(discord.ui.Select):
    """右鍵選單用的目標語言下拉。"""

    def __init__(self, cog: "Translator", text: str):
        self.cog = cog
        self.text = text
        options = [
            discord.SelectOption(label=name, value=code) for name, code in COMMON_LANGS
        ]
        super().__init__(placeholder="選擇要翻譯成的語言…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        target = self.values[0]
        # 沿用同一則 ephemeral 訊息:先 defer 顯示載入,翻完再 edit
        await interaction.response.defer()
        src_code = detect_flores(self.text) or "eng_Latn"
        try:
            translated = await self.cog.engine.translate(self.text, src_code, target)
        except Exception as e:
            logger.error(f"右鍵選單翻譯失敗：{e.__class__.__name__} - {e}")
            await interaction.edit_original_response(
                content=f"❌ 翻譯失敗：{e.__class__.__name__}", view=None
            )
            return
        embed = self.cog.build_embed(self.text, translated, src_code, target)
        await interaction.edit_original_response(content=None, embed=embed, view=None)


class TargetLangView(discord.ui.View):
    def __init__(self, cog: "Translator", text: str):
        super().__init__(timeout=120)
        self.add_item(TargetLangSelect(cog, text))


class Translator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.engine = NLLBEngine(NLLB_MODEL_NAME, NLLB_DEVICE)
        self.auto_channels: dict[str, str] = {}  # {channel_id(str): FLORES 目標碼}
        self.load_config()
        self._ctx_menu = app_commands.ContextMenu(name="翻譯", callback=self.translate_message)

    async def cog_load(self):
        self.bot.tree.add_command(self._ctx_menu)
        logger.info("已註冊右鍵選單指令「翻譯」")

    async def cog_unload(self):
        self.bot.tree.remove_command(self._ctx_menu.name, type=self._ctx_menu.type)

    # === 設定檔(仿 forwarder) ===
    def load_config(self):
        if os.path.exists(TRANSLATE_CHANNELS_FILE):
            try:
                with open(TRANSLATE_CHANNELS_FILE, "r", encoding="utf-8") as f:
                    self.auto_channels = json.load(f)
                logger.info(f"載入 {len(self.auto_channels)} 個自動翻譯頻道設定")
            except Exception as e:
                logger.warning(f"讀取 {TRANSLATE_CHANNELS_FILE} 失敗：{e}，使用空設定")
                self.auto_channels = {}
        else:
            logger.warning(f"{TRANSLATE_CHANNELS_FILE} 不存在,將使用空設定")
            self.auto_channels = {}

    def save_config(self):
        os.makedirs(os.path.dirname(TRANSLATE_CHANNELS_FILE), exist_ok=True)
        with open(TRANSLATE_CHANNELS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.auto_channels, f, ensure_ascii=False, indent=4)
        logger.info("自動翻譯頻道設定已儲存")

    # === 共用工具 ===
    def build_embed(self, original: str, translated: str, src_code: str, tgt_code: str) -> discord.Embed:
        src_name = FLORES_TO_NAME.get(src_code, src_code)
        tgt_name = FLORES_TO_NAME.get(tgt_code, tgt_code)
        embed = discord.Embed(
            title=f"🌐 {src_name} → {tgt_name}",
            description=(translated or "(無輸出)")[:4096],
            color=EMBED_COLOR,
        )
        if original:
            embed.add_field(name="原文", value=original[:1024], inline=False)
        return embed

    async def _translate_and_reply(self, message: discord.Message, src_code: str, tgt_code: str):
        """翻譯並以進度訊息回覆(供反應 / 自動頻道共用)。"""
        progress = await message.reply("⏳ 翻譯中…", mention_author=False)
        try:
            translated = await self.engine.translate(message.content, src_code, tgt_code)
        except Exception as e:
            logger.error(f"翻譯失敗：{e.__class__.__name__} - {e}")
            await progress.edit(content=f"❌ 翻譯失敗：{e.__class__.__name__}")
            return
        embed = self.build_embed(message.content, translated, src_code, tgt_code)
        await progress.edit(content=None, embed=embed)

    # === 觸發 1:右鍵訊息選單 ===
    async def translate_message(self, interaction: discord.Interaction, message: discord.Message):
        if not message.content:
            await interaction.response.send_message("⚠️ 此訊息沒有可翻譯的文字。", ephemeral=True)
            return
        await interaction.response.send_message(
            content="請選擇要翻譯成的語言：",
            view=TargetLangView(self, message.content),
            ephemeral=True,
        )

    # === 觸發 2:旗幟 emoji 反應 ===
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        target = FLAG_TO_FLORES.get(str(payload.emoji))
        if not target:
            return
        try:
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
        except Exception as e:
            logger.warning(f"取得反應訊息失敗：{e.__class__.__name__} - {e}")
            return

        if not message.content:
            return
        src_code = detect_flores(message.content)
        if src_code == target:  # 同語言不重複翻譯
            return
        await self._translate_and_reply(message, src_code or "eng_Latn", target)

    # === 觸發 3:自動翻譯頻道 ===
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.content:
            return
        if message.content.startswith("!"):  # 指令交給指令系統,不翻譯
            return
        target = self.auto_channels.get(str(message.channel.id))
        if not target:
            return
        src_code = detect_flores(message.content)
        if src_code is None or src_code == target:  # 偵測不到或同語言則略過
            return
        await self._translate_and_reply(message, src_code, target)

    # === 管理指令(owner-only) ===
    @commands.hybrid_command(name="set_translate_channel")
    @commands.is_owner()
    @app_commands.describe(channel="要自動翻譯的頻道", lang="目標語言")
    @app_commands.choices(
        lang=[app_commands.Choice(name=name, value=code) for name, code in COMMON_LANGS]
    )
    async def set_translate_channel(self, ctx: commands.Context, channel: discord.TextChannel, lang: str):
        """設定某頻道的訊息自動翻譯成指定語言 (owner-only)。"""
        self.auto_channels[str(channel.id)] = lang
        self.save_config()
        await ctx.send(f"✅ 已設定 {channel.mention} 自動翻譯為 **{FLORES_TO_NAME.get(lang, lang)}**")

    @commands.hybrid_command(name="unset_translate_channel")
    @commands.is_owner()
    @app_commands.describe(channel="要取消自動翻譯的頻道")
    async def unset_translate_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """取消某頻道的自動翻譯 (owner-only)。"""
        if str(channel.id) in self.auto_channels:
            del self.auto_channels[str(channel.id)]
            self.save_config()
            await ctx.send(f"✅ 已取消 {channel.mention} 的自動翻譯")
        else:
            await ctx.send(f"⚠️ {channel.mention} 並未設定自動翻譯")

    @commands.hybrid_command(name="translate_status")
    async def translate_status(self, ctx: commands.Context):
        """顯示自動翻譯頻道設定與模型載入狀態。"""
        embed = discord.Embed(title="🌐 翻譯功能狀態", color=EMBED_COLOR)
        loaded = "✅ 已載入" if self.engine.is_loaded else "💤 未載入(首次翻譯時才載入)"
        embed.add_field(
            name="NLLB 模型",
            value=f"{NLLB_MODEL_NAME}\n狀態：{loaded}" + (f"(device={self.engine.device})" if self.engine.is_loaded else ""),
            inline=False,
        )
        if self.auto_channels:
            lines = [
                f"<#{cid}> ➜ {FLORES_TO_NAME.get(code, code)}"
                for cid, code in self.auto_channels.items()
            ]
            embed.add_field(name=f"自動翻譯頻道（{len(self.auto_channels)}）", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="自動翻譯頻道", value="(尚未設定)", inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Translator(bot))
