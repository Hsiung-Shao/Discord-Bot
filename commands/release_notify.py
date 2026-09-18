"""專案版本更新公告推送。

多專案訂閱制:每個專案一筆設定(來源 + 推送目標 + 通知身分組),
全部用 /release 系列 slash 指令管理,設定存 data/release_notify.json,改完即時生效不需重啟。

支援三種來源(source_type):
  github  — 輪詢 GitHub Releases API,有新 release 自動推送
  file    — 讀本地 JSON 檔(路徑可設可不設,不設就等同停用此來源)
  manual  — 不自動抓,只靠 /release publish 手動發布

去重:GitHub 用 tag_name、檔案用 version 當 key,記在 state.sent_keys(保留最近 50 筆)。
新增專案時走 seed 模式(既有版本全部記為已推送但不發),避免一次灌爆頻道。
"""

import os
import json
import asyncio
from datetime import datetime, timezone
from typing import Optional, Union

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    RELEASE_NOTIFY_FILE,
    GITHUB_TOKEN,
    RELEASE_CHECK_INTERVAL_MINUTES,
)
import logging
from utils.logger import get_logger

logger = get_logger("ReleaseNotify", level=logging.WARNING)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_PER_PAGE = 10

# 單輪單專案最多推送則數,超出的下一輪續推(每則推完即寫檔,天然斷點續傳)
MAX_PUSH_PER_RUN = 5

# 去重清單保留筆數
MAX_SENT_KEYS = 50

# Embed description 上限 4096,留餘裕給截斷提示
BODY_LIMIT = 3900

DEFAULT_COLOR = 0x5865F2

SOURCE_LABELS = {
    "github": "GitHub Releases",
    "file": "本地檔案",
    "manual": "手動發布",
}

# 推送時允許 mention 身分組,但擋掉 @everyone/@here 與個人 mention
ALLOWED_MENTIONS = discord.AllowedMentions(roles=True, users=False, everyone=False)

# 可作為推送目標的頻道型別(專案大量使用討論串,不能只收 TextChannel)
TargetChannel = Union[discord.TextChannel, discord.Thread]


class ReleaseSourceError(Exception):
    """來源抓取/解析失敗,訊息會直接回給指令操作者。"""


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


def _parse_dt(raw) -> Optional[datetime]:
    """把 GitHub 的 ISO8601 或使用者寫的日期字串轉成 datetime,失敗回 None。"""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip().replace("Z", "+00:00")
    for parser in (
        lambda s: datetime.fromisoformat(s),
        lambda s: datetime.strptime(s, "%Y/%m/%d"),
        lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            dt = parser(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _truncate_body(text: str, url: Optional[str], limit: int = BODY_LIMIT) -> str:
    """超長內容在換行處截斷,並附上原文連結。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    split_at = cut.rfind("\n")
    if split_at > limit * 0.6:
        cut = cut[:split_at]
    suffix = "\n\n…(內容過長已截斷)"
    if url:
        suffix += f" [查看完整更新內容]({url})"
    return cut.rstrip() + suffix


class PublishModal(discord.ui.Modal, title="發布更新公告"):
    """手動發布用的彈出視窗(Discord Modal 上限 5 欄,這裡用 4 欄)。"""

    version = discord.ui.TextInput(
        label="版本號", placeholder="例如 v1.2.0", max_length=100, required=True
    )
    headline = discord.ui.TextInput(
        label="標題(選填)", placeholder="例如 新增自動翻譯功能",
        max_length=200, required=False
    )
    body = discord.ui.TextInput(
        label="更新內容", style=discord.TextStyle.paragraph,
        placeholder="支援 Markdown:## 標題、- 條列、**粗體**、```程式碼```",
        max_length=4000, required=True
    )
    link = discord.ui.TextInput(
        label="連結(選填)", placeholder="https://github.com/owner/repo/releases/tag/v1.2.0",
        max_length=500, required=False
    )

    def __init__(self, cog: "ReleaseNotify", project_key: str):
        super().__init__()
        self.cog = cog
        self.project_key = project_key

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        version = str(self.version).strip()
        url = str(self.link).strip() or None
        item = {
            "key": version,
            "version": version,
            "title": str(self.headline).strip() or version,
            "url": url,
            "body": str(self.body).strip(),
            "date": datetime.now(timezone.utc),
            "source_label": SOURCE_LABELS["manual"],
        }

        project = self.cog.config.get("projects", {}).get(self.project_key)
        if project is None:
            await interaction.followup.send(f"❌ 專案 `{self.project_key}` 已不存在。", ephemeral=True)
            return

        sent, failed = await self.cog._push_release(self.project_key, project, item)
        if sent:
            self.cog._mark_sent(project, item["key"])
            self.cog._save_config()

        result = f"✅ 已發布 **{version}** 到 {sent} 個頻道。"
        if failed:
            result += f"(有 {failed} 個頻道失敗,詳見 logs/release_notify.log)"
        if not sent:
            result = "❌ 沒有任何頻道發送成功,請先用 `/release target_add` 設定推送頻道。"
        await interaction.followup.send(result, ephemeral=True)


class ReleaseNotify(commands.Cog):
    # Group 必須是 class attribute,discord.py 才會把底下的子指令綁到這個 cog
    release_group = app_commands.Group(
        name="release",
        description="專案版本更新公告管理",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.config = self._load_config()
        self._check_lock = asyncio.Lock()

    # ────────────────────────── 設定檔存取 ──────────────────────────

    def _default_config(self) -> dict:
        return {
            "projects": {},
            "settings": {"interval_minutes": RELEASE_CHECK_INTERVAL_MINUTES},
        }

    def _load_config(self) -> dict:
        if not os.path.exists(RELEASE_NOTIFY_FILE):
            config = self._default_config()
            self._save_config(config)
            return config
        try:
            with open(RELEASE_NOTIFY_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"載入更新公告設定失敗,改用空設定: {e}")
            return self._default_config()

        config.setdefault("projects", {})
        config.setdefault("settings", {"interval_minutes": RELEASE_CHECK_INTERVAL_MINUTES})
        return config

    def _save_config(self, config: Optional[dict] = None):
        if config is None:
            config = self.config
        directory = os.path.dirname(RELEASE_NOTIFY_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(RELEASE_NOTIFY_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

    def _get_project(self, key: str) -> Optional[dict]:
        return self.config.get("projects", {}).get(key)

    @staticmethod
    def _state(project: dict) -> dict:
        return project.setdefault("state", {"sent_keys": [], "etag": None, "last_checked": None})

    def _mark_sent(self, project: dict, key: str):
        state = self._state(project)
        sent = state.setdefault("sent_keys", [])
        if key not in sent:
            sent.append(key)
        if len(sent) > MAX_SENT_KEYS:
            del sent[:-MAX_SENT_KEYS]

    # ────────────────────────── 排程 ──────────────────────────

    async def cog_load(self):
        interval = self.config.get("settings", {}).get(
            "interval_minutes", RELEASE_CHECK_INTERVAL_MINUTES
        )
        try:
            interval = max(1, int(interval))
        except (TypeError, ValueError):
            interval = RELEASE_CHECK_INTERVAL_MINUTES
        self.scheduler.add_job(self._scheduled_check, IntervalTrigger(minutes=interval))
        self.scheduler.start()
        logger.info(f"更新公告排程已啟動,每 {interval} 分鐘檢查一次")

    def cog_unload(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def _scheduled_check(self):
        await self.bot.wait_until_ready()
        try:
            await self._check_all_projects()
        except Exception as e:
            logger.error(f"排程檢查發生未預期錯誤: {e}")

    async def _check_all_projects(self) -> dict:
        """檢查所有啟用中的專案,回傳 {專案key: 推送則數 或 錯誤字串}。"""
        async with self._check_lock:
            results = {}
            for key, project in list(self.config.get("projects", {}).items()):
                if not project.get("enabled", True):
                    continue
                if project.get("source_type") == "manual":
                    continue
                try:
                    results[key] = await self._check_project(key, project)
                except ReleaseSourceError as e:
                    results[key] = f"錯誤: {e}"
                    logger.warning(f"[{key}] 檢查失敗: {e}")
                except Exception as e:
                    results[key] = f"錯誤: {e}"
                    logger.error(f"[{key}] 檢查發生未預期錯誤: {e}")
            self._save_config()
            return results

    async def _check_project(self, key: str, project: dict) -> int:
        """抓來源 → 過濾已推送 → 逐則推送,回傳實際推送則數。"""
        items = await self._fetch_source(project)
        state = self._state(project)
        state["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if items is None:  # 304 Not Modified,內容沒變
            return 0

        sent_keys = set(state.get("sent_keys", []))
        new_items = [it for it in items if it["key"] not in sent_keys]
        if not new_items:
            return 0

        if len(new_items) > MAX_PUSH_PER_RUN:
            logger.info(
                f"[{key}] 新版本 {len(new_items)} 則超過單輪上限 {MAX_PUSH_PER_RUN},其餘下輪續推"
            )
            new_items = new_items[:MAX_PUSH_PER_RUN]

        pushed = 0
        for item in new_items:
            sent, failed = await self._push_release(key, project, item)
            if sent:
                self._mark_sent(project, item["key"])
                self._save_config()
                pushed += 1
                logger.info(f"[{key}] 已推送 {item['version']} 到 {sent} 個頻道(失敗 {failed})")
            else:
                logger.warning(f"[{key}] {item['version']} 沒有任何頻道發送成功,下輪重試")
                break  # 全數失敗多半是設定問題,不用把後面的也打完
            await asyncio.sleep(1)
        return pushed

    # ────────────────────────── 來源抓取 ──────────────────────────

    async def _fetch_source(self, project: dict) -> Optional[list]:
        """依 source_type 取得標準化的版本清單(舊→新)。None 代表內容未變更。"""
        source_type = project.get("source_type", "github")
        if source_type == "github":
            return await self._fetch_github_releases(project)
        if source_type == "file":
            return self._read_file_source(project)
        return []

    async def _fetch_github_releases(self, project: dict) -> Optional[list]:
        repo = (project.get("repo") or "").strip().strip("/")
        if not repo:
            raise ReleaseSourceError("尚未設定 GitHub repo(格式 owner/repo)")

        url = f"{GITHUB_API_BASE}/repos/{repo}/releases?per_page={GITHUB_PER_PAGE}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "DiscordBot-ReleaseNotify",
        }
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        etag = self._state(project).get("etag")
        if etag:
            headers["If-None-Match"] = etag

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 304:
                        return None
                    if resp.status == 404:
                        raise ReleaseSourceError(
                            f"找不到 repo `{repo}`(private repo 需在 .env 設定 GITHUB_TOKEN)"
                        )
                    if resp.status in (401, 403):
                        remaining = resp.headers.get("X-RateLimit-Remaining")
                        if remaining == "0":
                            reset = resp.headers.get("X-RateLimit-Reset", "")
                            raise ReleaseSourceError(
                                f"GitHub API 額度用盡(reset={reset}),本輪跳過。"
                                "建議在 .env 設定 GITHUB_TOKEN 以提高額度"
                            )
                        raise ReleaseSourceError(f"GitHub API 拒絕存取(HTTP {resp.status}),請檢查 GITHUB_TOKEN")
                    if resp.status != 200:
                        raise ReleaseSourceError(f"GitHub API 回應 HTTP {resp.status}")
                    data = await resp.json(content_type=None)
                    new_etag = resp.headers.get("ETag")
        except aiohttp.ClientError as e:
            raise ReleaseSourceError(f"連線 GitHub 失敗: {e}")
        except asyncio.TimeoutError:
            raise ReleaseSourceError("連線 GitHub 逾時")

        if not isinstance(data, list):
            raise ReleaseSourceError("GitHub API 回應格式非預期")

        if new_etag:
            self._state(project)["etag"] = new_etag

        include_prerelease = project.get("include_prerelease", False)
        items = []
        for entry in data:
            if entry.get("draft"):
                continue
            if entry.get("prerelease") and not include_prerelease:
                continue
            tag = entry.get("tag_name") or entry.get("name")
            if not tag:
                continue
            items.append({
                "key": tag,
                "version": tag,
                "title": entry.get("name") or tag,
                "url": entry.get("html_url"),
                "body": entry.get("body") or "(這個版本沒有填寫更新說明)",
                "date": _parse_dt(entry.get("published_at") or entry.get("created_at")),
                "prerelease": bool(entry.get("prerelease")),
                "source_label": SOURCE_LABELS["github"],
            })

        # GitHub 回傳順序不保證,一律依發布時間排序(舊→新)
        items.sort(key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc))
        return items

    def _read_file_source(self, project: dict) -> list:
        path = (project.get("file_path") or "").strip()
        if not path:
            raise ReleaseSourceError("尚未設定更新資料檔案路徑(file_path)")
        if not os.path.exists(path):
            raise ReleaseSourceError(f"找不到檔案 `{path}`")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ReleaseSourceError(f"`{path}` 不是合法 JSON: {e}")
        except Exception as e:
            raise ReleaseSourceError(f"讀取 `{path}` 失敗: {e}")

        if isinstance(data, dict) and isinstance(data.get("releases"), list):
            entries = data["releases"]
        elif isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = [data]
        else:
            raise ReleaseSourceError(f"`{path}` 內容格式非預期(需為物件或 releases 陣列)")

        items = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            version = str(entry.get("version") or "").strip()
            if not version:
                continue
            items.append({
                "key": version,
                "version": version,
                "title": (entry.get("title") or "").strip() or version,
                "url": (entry.get("url") or "").strip() or None,
                "body": self._compose_file_body(entry),
                "date": _parse_dt(entry.get("date")),
                "source_label": SOURCE_LABELS["file"],
            })

        items.sort(key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc))
        return items

    @staticmethod
    def _compose_file_body(entry: dict) -> str:
        parts = []
        highlights = entry.get("highlights")
        if isinstance(highlights, list) and highlights:
            bullets = "\n".join(f"• {str(h).strip()}" for h in highlights if str(h).strip())
            if bullets:
                parts.append(f"✨ **本次重點**\n{bullets}")
        body = (entry.get("body") or "").strip()
        if body:
            parts.append(body)
        return "\n\n".join(parts) or "(沒有填寫更新說明)"

    # ────────────────────────── 推送 ──────────────────────────

    def _build_embed(self, project: dict, item: dict) -> discord.Embed:
        display_name = project.get("display_name") or "專案"
        version = item.get("version", "")
        title = f"📦 {display_name} {version}"
        headline = (item.get("title") or "").strip()
        if headline and headline != version:
            title = f"{title} — {headline}"
        if item.get("prerelease"):
            title = f"{title} (Pre-release)"

        embed = discord.Embed(
            title=title[:256],
            url=item.get("url") or None,
            description=_truncate_body(item.get("body", ""), item.get("url")),
            color=project.get("color", DEFAULT_COLOR),
        )
        dt = item.get("date")
        if isinstance(dt, datetime):
            embed.timestamp = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        footer = item.get("source_label") or SOURCE_LABELS.get(
            project.get("source_type", "github"), ""
        )
        repo = (project.get("repo") or "").strip()
        if repo and project.get("source_type") == "github":
            footer = f"{footer} · {repo}"
        embed.set_footer(text=footer[:2048] if footer else discord.utils.MISSING)
        return embed

    async def _push_release(self, key: str, project: dict, item: dict) -> tuple[int, int]:
        """推送到該專案所有目標,回傳 (成功數, 失敗數)。"""
        targets = project.get("targets", [])
        if not targets:
            logger.warning(f"[{key}] 沒有設定任何推送頻道,跳過")
            return 0, 0

        embed = self._build_embed(project, item)
        success = failed = 0
        for target in targets:
            if await self._send_to_target(key, target, embed):
                success += 1
            else:
                failed += 1
        return success, failed

    async def _send_to_target(self, key: str, target: dict, embed: discord.Embed) -> bool:
        channel_id = target.get("channel_id")
        if not channel_id:
            return False
        try:
            channel = await self.bot.fetch_channel(int(channel_id))
        except discord.NotFound:
            logger.error(f"[{key}] 找不到頻道 {channel_id}")
            return False
        except discord.Forbidden:
            logger.error(f"[{key}] 無權限存取頻道 {channel_id}")
            return False
        except Exception as e:
            logger.error(f"[{key}] 取得頻道 {channel_id} 失敗: {e}")
            return False

        role_ids = target.get("role_ids", []) or []
        # mention 必須放在 content;寫在 embed 裡的 <@&id> 不會觸發通知
        content = " ".join(f"<@&{rid}>" for rid in role_ids) or None

        try:
            await channel.send(content=content, embed=embed, allowed_mentions=ALLOWED_MENTIONS)
            return True
        except discord.Forbidden:
            logger.error(f"[{key}] 沒有頻道 {channel_id} 的發言權限")
        except Exception as e:
            logger.error(f"[{key}] 發送到頻道 {channel_id} 失敗: {e}")
        return False

    # ────────────────────────── 指令輔助 ──────────────────────────

    async def _project_autocomplete(self, interaction: discord.Interaction, current: str):
        keys = list(self.config.get("projects", {}).keys())
        current = (current or "").lower()
        return [
            app_commands.Choice(name=k, value=k)
            for k in keys if current in k.lower()
        ][:25]

    @staticmethod
    def _resolve_ids(
        channel: Optional[TargetChannel], channel_id: Optional[str],
        role: Optional[discord.Role], role_id: Optional[str],
    ) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """把「選擇器」與「手打 ID」兩種輸入收斂成 (channel_id, role_id, 錯誤訊息)。"""
        resolved_channel = channel.id if channel else None
        if channel_id:
            raw = channel_id.strip()
            if not raw.isdigit():
                return None, None, f"頻道 ID `{raw}` 不是純數字"
            resolved_channel = int(raw)

        resolved_role = role.id if role else None
        if role_id:
            raw = role_id.strip()
            if not raw.isdigit():
                return None, None, f"身分組 ID `{raw}` 不是純數字"
            resolved_role = int(raw)

        return resolved_channel, resolved_role, None

    @staticmethod
    def _add_target(project: dict, channel_id: int, role_id: Optional[int]) -> str:
        """把頻道/身分組寫進 targets(同頻道則合併身分組),回傳給使用者看的結果描述。"""
        targets = project.setdefault("targets", [])
        for target in targets:
            if target.get("channel_id") == channel_id:
                role_ids = target.setdefault("role_ids", [])
                if role_id is None:
                    return "頻道已在推送列表中(未變更身分組)"
                if role_id in role_ids:
                    return "頻道與身分組都已在推送列表中"
                role_ids.append(role_id)
                return "已為既有頻道追加通知身分組"
        targets.append({
            "channel_id": channel_id,
            "role_ids": [role_id] if role_id else [],
        })
        return "已新增推送目標"

    @staticmethod
    def _describe_targets(project: dict) -> str:
        targets = project.get("targets", [])
        if not targets:
            return "(尚未設定)"
        lines = []
        for target in targets:
            roles = target.get("role_ids", []) or []
            role_text = " ".join(f"<@&{r}>" for r in roles) if roles else "不 tag"
            lines.append(f"<#{target.get('channel_id')}> → {role_text}")
        return "\n".join(lines)

    def _describe_source(self, project: dict) -> str:
        source_type = project.get("source_type", "github")
        label = SOURCE_LABELS.get(source_type, source_type)
        if source_type == "github":
            repo = project.get("repo") or "(未設定)"
            pre = "含 pre-release" if project.get("include_prerelease") else "不含 pre-release"
            return f"{label}:`{repo}`({pre})"
        if source_type == "file":
            return f"{label}:`{project.get('file_path') or '(未設定路徑,此來源停用)'}`"
        return label

    async def _seed_project(self, key: str, project: dict) -> tuple[int, Optional[str]]:
        """把來源當下的既有版本全記為已推送但不發,回傳 (記錄筆數, 錯誤訊息)。"""
        if project.get("source_type") == "manual":
            return 0, None
        try:
            items = await self._fetch_source(project) or []
        except ReleaseSourceError as e:
            return 0, str(e)
        except Exception as e:
            return 0, f"未預期錯誤: {e}"

        state = self._state(project)
        state["sent_keys"] = [it["key"] for it in items][-MAX_SENT_KEYS:]
        state["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return len(items), None

    # ────────────────────────── Slash 指令 ──────────────────────────

    @release_group.command(name="add", description="新增一個要推送更新公告的專案")
    @app_commands.describe(
        key="專案代號(英數,之後指令都用它指定專案)",
        display_name="公告顯示名稱,例如「Discord Bot」",
        source="更新來源",
        channel="推送頻道",
        role="要 tag 通知的身分組(選填)",
        repo="GitHub repo,格式 owner/repo(source=github 時必填)",
        file_path="本地更新 JSON 的路徑(source=file 時必填,例如 data/myproject_release.json)",
        include_prerelease="是否也推送 pre-release(預設否)",
        color="Embed 顏色,例如 #5865F2(選填)",
        channel_id="跨伺服器頻道 ID(填了會蓋過 channel)",
        role_id="跨伺服器身分組 ID(填了會蓋過 role)",
    )
    @app_commands.choices(source=[
        app_commands.Choice(name="GitHub Releases(自動輪詢)", value="github"),
        app_commands.Choice(name="本地 JSON 檔案(自動輪詢)", value="file"),
        app_commands.Choice(name="僅手動發布(/release publish)", value="manual"),
    ])
    async def add_project(
        self,
        interaction: discord.Interaction,
        key: str,
        display_name: str,
        source: app_commands.Choice[str],
        channel: Optional[TargetChannel] = None,
        role: Optional[discord.Role] = None,
        repo: Optional[str] = None,
        file_path: Optional[str] = None,
        include_prerelease: bool = False,
        color: Optional[str] = None,
        channel_id: Optional[str] = None,
        role_id: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        key = key.strip().lower()
        if not key:
            await interaction.followup.send("❌ 專案代號不可為空。", ephemeral=True)
            return
        if key in self.config.get("projects", {}):
            await interaction.followup.send(f"❌ 專案代號 `{key}` 已存在,請換一個或用 `/release edit`。", ephemeral=True)
            return

        source_type = source.value
        if source_type == "github" and not (repo or "").strip():
            await interaction.followup.send("❌ 來源選 GitHub 時必須填 `repo`(格式 owner/repo)。", ephemeral=True)
            return
        if source_type == "file" and not (file_path or "").strip():
            await interaction.followup.send("❌ 來源選本地檔案時必須填 `file_path`。", ephemeral=True)
            return

        resolved_channel, resolved_role, error = self._resolve_ids(channel, channel_id, role, role_id)
        if error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return
        if not resolved_channel:
            await interaction.followup.send("❌ 請指定推送頻道(`channel` 或 `channel_id` 擇一)。", ephemeral=True)
            return

        project = {
            "display_name": display_name.strip(),
            "source_type": source_type,
            "repo": (repo or "").strip().strip("/") or None,
            "include_prerelease": include_prerelease,
            "file_path": (file_path or "").strip() or None,
            "color": _parse_color(color),
            "enabled": True,
            "targets": [{"channel_id": resolved_channel, "role_ids": [resolved_role] if resolved_role else []}],
            "state": {"sent_keys": [], "etag": None, "last_checked": None},
        }

        seeded, seed_error = await self._seed_project(key, project)
        self.config.setdefault("projects", {})[key] = project
        self._save_config()

        lines = [
            f"✅ 已新增專案 **{project['display_name']}**(代號 `{key}`)",
            f"　來源:{self._describe_source(project)}",
            f"　推送:{self._describe_targets(project)}",
        ]
        if seed_error:
            lines.append(f"⚠️ 來源檢查失敗:{seed_error}")
            lines.append("　設定已存檔,修正後可用 `/release check` 重試。")
        elif source_type == "manual":
            lines.append("　此專案不自動抓取,請用 `/release publish` 發布公告。")
        else:
            lines.append(f"　已記錄 {seeded} 筆既有版本(不補推),之後有新版本才會自動推送。")
            lines.append("　想立刻試推最新一筆:`/release check` 的 `resend_latest` 設為 True。")
        if resolved_role:
            lines.append("　⚠️ 身分組若沒開「允許任何人 @ 提及」,通知可能不會跳,先用 `/release test` 驗證。")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @release_group.command(name="remove", description="移除一個專案的更新公告設定")
    @app_commands.describe(key="專案代號")
    @app_commands.autocomplete(key=_project_autocomplete)
    async def remove_project(self, interaction: discord.Interaction, key: str):
        key = key.strip().lower()
        project = self._get_project(key)
        if project is None:
            await interaction.response.send_message(f"❌ 找不到專案 `{key}`。", ephemeral=True)
            return
        del self.config["projects"][key]
        self._save_config()
        await interaction.response.send_message(
            f"✅ 已移除專案 **{project.get('display_name', key)}**(代號 `{key}`)。", ephemeral=True
        )

    @release_group.command(name="list", description="列出所有更新公告專案")
    async def list_projects(self, interaction: discord.Interaction):
        projects = self.config.get("projects", {})
        if not projects:
            await interaction.response.send_message(
                "📭 目前沒有任何專案,用 `/release add` 新增一個。", ephemeral=True
            )
            return

        interval = self.config.get("settings", {}).get("interval_minutes", RELEASE_CHECK_INTERVAL_MINUTES)
        embed = discord.Embed(
            title="📦 更新公告專案清單",
            description=f"自動檢查間隔:每 {interval} 分鐘"
                        + ("" if GITHUB_TOKEN else "\n⚠️ 未設定 GITHUB_TOKEN,GitHub API 額度僅 60 次/小時"),
            color=DEFAULT_COLOR,
        )
        for key, project in list(projects.items())[:25]:
            state = project.get("state", {})
            sent_keys = state.get("sent_keys", [])
            latest = sent_keys[-1] if sent_keys else "(尚未推送過)"
            status = "🟢 啟用" if project.get("enabled", True) else "⚪ 停用"
            value = (
                f"{status}｜來源:{self._describe_source(project)}\n"
                f"最新已推送:`{latest}`｜最後檢查:{state.get('last_checked') or '尚未檢查'}\n"
                f"{self._describe_targets(project)}"
            )
            embed.add_field(
                name=f"{project.get('display_name', key)}(`{key}`)",
                value=value[:1024],
                inline=False,
            )
        if len(projects) > 25:
            embed.set_footer(text=f"共 {len(projects)} 個專案,僅顯示前 25 個")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @release_group.command(name="target_add", description="為專案新增推送頻道 / 通知身分組")
    @app_commands.describe(
        key="專案代號",
        channel="推送頻道",
        role="要 tag 通知的身分組(選填)",
        channel_id="跨伺服器頻道 ID(填了會蓋過 channel)",
        role_id="跨伺服器身分組 ID(填了會蓋過 role)",
    )
    @app_commands.autocomplete(key=_project_autocomplete)
    async def target_add(
        self,
        interaction: discord.Interaction,
        key: str,
        channel: Optional[TargetChannel] = None,
        role: Optional[discord.Role] = None,
        channel_id: Optional[str] = None,
        role_id: Optional[str] = None,
    ):
        key = key.strip().lower()
        project = self._get_project(key)
        if project is None:
            await interaction.response.send_message(f"❌ 找不到專案 `{key}`。", ephemeral=True)
            return

        resolved_channel, resolved_role, error = self._resolve_ids(channel, channel_id, role, role_id)
        if error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        if not resolved_channel:
            await interaction.response.send_message("❌ 請指定頻道(`channel` 或 `channel_id` 擇一)。", ephemeral=True)
            return

        result = self._add_target(project, resolved_channel, resolved_role)
        self._save_config()
        await interaction.response.send_message(
            f"✅ {result}\n{self._describe_targets(project)}", ephemeral=True
        )

    @release_group.command(name="target_remove", description="移除專案的推送頻道,或只移除某個通知身分組")
    @app_commands.describe(
        key="專案代號",
        channel="要移除的頻道",
        role="只填此欄時,保留頻道但取消 tag 這個身分組",
        channel_id="跨伺服器頻道 ID(填了會蓋過 channel)",
        role_id="跨伺服器身分組 ID(填了會蓋過 role)",
    )
    @app_commands.autocomplete(key=_project_autocomplete)
    async def target_remove(
        self,
        interaction: discord.Interaction,
        key: str,
        channel: Optional[TargetChannel] = None,
        role: Optional[discord.Role] = None,
        channel_id: Optional[str] = None,
        role_id: Optional[str] = None,
    ):
        key = key.strip().lower()
        project = self._get_project(key)
        if project is None:
            await interaction.response.send_message(f"❌ 找不到專案 `{key}`。", ephemeral=True)
            return

        resolved_channel, resolved_role, error = self._resolve_ids(channel, channel_id, role, role_id)
        if error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        if not resolved_channel:
            await interaction.response.send_message("❌ 請指定頻道(`channel` 或 `channel_id` 擇一)。", ephemeral=True)
            return

        targets = project.get("targets", [])
        target = next((t for t in targets if t.get("channel_id") == resolved_channel), None)
        if target is None:
            await interaction.response.send_message(f"ℹ️ <#{resolved_channel}> 不在 `{key}` 的推送列表中。", ephemeral=True)
            return

        if resolved_role:
            role_ids = target.get("role_ids", []) or []
            if resolved_role not in role_ids:
                await interaction.response.send_message(
                    f"ℹ️ <@&{resolved_role}> 不在 <#{resolved_channel}> 的通知身分組中。", ephemeral=True
                )
                return
            role_ids.remove(resolved_role)
            target["role_ids"] = role_ids
            message = f"✅ 已取消在 <#{resolved_channel}> tag <@&{resolved_role}>。"
        else:
            targets.remove(target)
            message = f"✅ 已將 <#{resolved_channel}> 從 `{key}` 的推送列表移除。"

        self._save_config()
        await interaction.response.send_message(
            f"{message}\n{self._describe_targets(project)}",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @release_group.command(name="edit", description="修改專案設定(只填要改的欄位)")
    @app_commands.describe(
        key="專案代號",
        display_name="公告顯示名稱",
        source="更新來源",
        repo="GitHub repo,格式 owner/repo",
        file_path="本地更新 JSON 路徑;填 `-` 可清除(清除後檔案來源停用)",
        include_prerelease="是否也推送 pre-release",
        color="Embed 顏色,例如 #5865F2",
        enabled="是否啟用自動檢查",
    )
    @app_commands.autocomplete(key=_project_autocomplete)
    @app_commands.choices(source=[
        app_commands.Choice(name="GitHub Releases(自動輪詢)", value="github"),
        app_commands.Choice(name="本地 JSON 檔案(自動輪詢)", value="file"),
        app_commands.Choice(name="僅手動發布(/release publish)", value="manual"),
    ])
    async def edit_project(
        self,
        interaction: discord.Interaction,
        key: str,
        display_name: Optional[str] = None,
        source: Optional[app_commands.Choice[str]] = None,
        repo: Optional[str] = None,
        file_path: Optional[str] = None,
        include_prerelease: Optional[bool] = None,
        color: Optional[str] = None,
        enabled: Optional[bool] = None,
    ):
        key = key.strip().lower()
        project = self._get_project(key)
        if project is None:
            await interaction.response.send_message(f"❌ 找不到專案 `{key}`。", ephemeral=True)
            return

        changes = []
        if display_name:
            project["display_name"] = display_name.strip()
            changes.append("顯示名稱")
        if source:
            project["source_type"] = source.value
            changes.append(f"來源改為 {SOURCE_LABELS.get(source.value, source.value)}")
        if repo:
            project["repo"] = repo.strip().strip("/")
            project.setdefault("state", {})["etag"] = None  # 換 repo 後舊 ETag 失效
            changes.append("GitHub repo")
        if file_path:
            cleaned = file_path.strip()
            if cleaned == "-":
                project["file_path"] = None
                changes.append("已清除檔案路徑(檔案來源停用)")
            else:
                project["file_path"] = cleaned
                changes.append("檔案路徑")
        if include_prerelease is not None:
            project["include_prerelease"] = include_prerelease
            changes.append(f"pre-release {'推送' if include_prerelease else '不推送'}")
        if color:
            project["color"] = _parse_color(color, project.get("color", DEFAULT_COLOR))
            changes.append("顏色")
        if enabled is not None:
            project["enabled"] = enabled
            changes.append("啟用" if enabled else "停用")

        if not changes:
            await interaction.response.send_message("ℹ️ 沒有指定任何要修改的欄位。", ephemeral=True)
            return

        self._save_config()
        await interaction.response.send_message(
            f"✅ 已更新 `{key}`:{'、'.join(changes)}\n　來源:{self._describe_source(project)}",
            ephemeral=True,
        )

    @release_group.command(name="check", description="立即檢查更新(不指定專案則檢查全部)")
    @app_commands.describe(
        key="專案代號(留空=全部)",
        resend_latest="忽略去重,直接把來源最新一筆重推一次",
    )
    @app_commands.autocomplete(key=_project_autocomplete)
    async def check_now(
        self,
        interaction: discord.Interaction,
        key: Optional[str] = None,
        resend_latest: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)

        if resend_latest:
            if not key:
                await interaction.followup.send("❌ `resend_latest` 必須指定專案代號。", ephemeral=True)
                return
            key = key.strip().lower()
            project = self._get_project(key)
            if project is None:
                await interaction.followup.send(f"❌ 找不到專案 `{key}`。", ephemeral=True)
                return
            try:
                items = await self._fetch_source(project)
            except ReleaseSourceError as e:
                await interaction.followup.send(f"❌ 抓取來源失敗:{e}", ephemeral=True)
                return
            if not items:
                await interaction.followup.send(
                    "ℹ️ 來源沒有可推送的版本(內容未變更或清單為空)。", ephemeral=True
                )
                return
            item = items[-1]
            sent, failed = await self._push_release(key, project, item)
            self._mark_sent(project, item["key"])
            self._save_config()
            await interaction.followup.send(
                f"{'✅' if sent else '❌'} 重推 **{item['version']}**:成功 {sent} 個頻道、失敗 {failed} 個。",
                ephemeral=True,
            )
            return

        if key:
            key = key.strip().lower()
            project = self._get_project(key)
            if project is None:
                await interaction.followup.send(f"❌ 找不到專案 `{key}`。", ephemeral=True)
                return
            if project.get("source_type") == "manual":
                await interaction.followup.send(
                    f"ℹ️ `{key}` 是手動發布專案,請用 `/release publish`。", ephemeral=True
                )
                return
            try:
                pushed = await self._check_project(key, project)
                result = f"✅ `{key}` 檢查完成,推送了 {pushed} 則。" if pushed else f"ℹ️ `{key}` 沒有新版本。"
            except ReleaseSourceError as e:
                result = f"❌ `{key}` 檢查失敗:{e}"
            except Exception as e:
                result = f"❌ `{key}` 檢查發生未預期錯誤:{e}"
                logger.error(f"[{key}] 手動檢查失敗: {e}")
            self._save_config()
            await interaction.followup.send(result, ephemeral=True)
            return

        results = await self._check_all_projects()
        if not results:
            await interaction.followup.send("ℹ️ 沒有啟用中的自動檢查專案。", ephemeral=True)
            return
        lines = []
        for pkey, value in results.items():
            if isinstance(value, int):
                lines.append(f"`{pkey}`:{'推送 ' + str(value) + ' 則' if value else '無新版本'}")
            else:
                lines.append(f"`{pkey}`:❌ {value}")
        await interaction.followup.send("檢查完成:\n" + "\n".join(lines), ephemeral=True)

    @release_group.command(name="publish", description="手動發布一則更新公告(彈出視窗填寫)")
    @app_commands.describe(key="專案代號")
    @app_commands.autocomplete(key=_project_autocomplete)
    async def publish(self, interaction: discord.Interaction, key: str):
        key = key.strip().lower()
        project = self._get_project(key)
        if project is None:
            await interaction.response.send_message(f"❌ 找不到專案 `{key}`。", ephemeral=True)
            return
        if not project.get("targets"):
            await interaction.response.send_message(
                f"❌ `{key}` 還沒有推送頻道,請先用 `/release target_add` 設定。", ephemeral=True
            )
            return
        await interaction.response.send_modal(PublishModal(self, key))

    @release_group.command(name="test", description="發一則測試公告,驗證頻道權限與身分組通知是否生效")
    @app_commands.describe(
        key="專案代號",
        channel="只測這個頻道(留空=測該專案所有推送目標)",
    )
    @app_commands.autocomplete(key=_project_autocomplete)
    async def test_push(
        self,
        interaction: discord.Interaction,
        key: str,
        channel: Optional[TargetChannel] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        key = key.strip().lower()
        project = self._get_project(key)
        if project is None:
            await interaction.followup.send(f"❌ 找不到專案 `{key}`。", ephemeral=True)
            return

        test_item = {
            "key": "__TEST__",
            "version": "v0.0.0-test",
            "title": "推送測試",
            "url": None,
            "body": (
                "這是一則測試公告,用來確認:\n"
                "- 我在這個頻道有發言權限\n"
                "- 設定的身分組真的會跳通知\n\n"
                "如果上面沒有出現 @身分組 的黃色高亮提醒,"
                "請到伺服器設定把該身分組的「允許任何人 @ 提及」打開。"
            ),
            "date": datetime.now(timezone.utc),
            "source_label": "測試訊息",
        }
        embed = self._build_embed(project, test_item)

        if channel:
            target = next(
                (t for t in project.get("targets", []) if t.get("channel_id") == channel.id),
                {"channel_id": channel.id, "role_ids": []},
            )
            ok = await self._send_to_target(key, target, embed)
            await interaction.followup.send(
                f"{'✅ 測試訊息已送出到' if ok else '❌ 發送失敗'} {channel.mention}"
                + ("" if ok else ",詳見 logs/release_notify.log"),
                ephemeral=True,
            )
            return

        targets = project.get("targets", [])
        if not targets:
            await interaction.followup.send(
                f"❌ `{key}` 沒有設定推送頻道,請先用 `/release target_add`。", ephemeral=True
            )
            return

        success = sum([await self._send_to_target(key, t, embed) for t in targets])
        await interaction.followup.send(
            f"測試完成:成功 {success} 個、失敗 {len(targets) - success} 個頻道。", ephemeral=True
        )

    @release_group.command(name="interval", description="設定自動檢查間隔(分鐘)")
    @app_commands.describe(minutes="檢查間隔分鐘數(建議 15 以上,避免打爆 GitHub API 額度)")
    async def set_interval(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 1440]):
        self.config.setdefault("settings", {})["interval_minutes"] = minutes
        self._save_config()

        # 重建排程,不必重啟 bot
        try:
            self.scheduler.remove_all_jobs()
            self.scheduler.add_job(self._scheduled_check, IntervalTrigger(minutes=minutes))
            logger.info(f"更新公告檢查間隔已改為 {minutes} 分鐘")
        except Exception as e:
            logger.error(f"重建排程失敗: {e}")
            await interaction.response.send_message(
                f"⚠️ 設定已存檔({minutes} 分鐘),但排程重建失敗,重啟 bot 後生效:{e}", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ 自動檢查間隔已改為每 {minutes} 分鐘(即時生效)。", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ReleaseNotify(bot))
