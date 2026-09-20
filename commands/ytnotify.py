import asyncio
import json
import os
import xml.etree.ElementTree as ET
from typing import Optional, Union

import aiohttp
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from utils.logger import get_logger
from config import (
    YT_CHANNEL_ID,
    YT_NEWS_THREAD_ID,
    YT_NOTIFY_ROLE_ID,
    YT_DATA_FILE,
    YT_CHECK_INTERVAL_MINUTES,
)

logger = get_logger("YTNotify", channel="ytnotify")

ALLOWED_MENTIONS = discord.AllowedMentions(roles=True, users=False, everyone=False)
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={YT_CHANNEL_ID}"
ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}
MAX_PUSH_PER_RUN = 5
MAX_KEPT_IDS = 100  # RSS 只回最新約 15 筆，留夠餘裕防止設定改動時誤判

TargetChannel = Union[discord.TextChannel, discord.Thread]  # 專案慣例：*_THREAD_ID 常是討論串


class YTNotify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()

    async def cog_load(self):
        self.scheduler.add_job(
            self._scheduled_check, IntervalTrigger(minutes=YT_CHECK_INTERVAL_MINUTES)
        )
        self.scheduler.start()
        logger.info(f"YTNotify 排程已啟動，每 {YT_CHECK_INTERVAL_MINUTES} 分鐘檢查一次")

    def cog_unload(self):
        self.scheduler.shutdown(wait=False)

    # ── 資料抓取 ──
    async def _fetch_feed_xml(self) -> Optional[str]:
        if not YT_CHANNEL_ID:
            logger.warning("YT_CHANNEL_ID 未設定，略過檢查")
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    FEED_URL, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"RSS 回應非 200：{resp.status}")
                        return None
                    return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"抓取 RSS 失敗：{e}")
            return None

    def _parse_feed(self, xml_text: str) -> list[dict]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f"RSS 解析失敗：{e}")
            return []
        items = []
        for entry in root.findall("a:entry", ATOM_NS):
            video_id = entry.findtext("yt:videoId", default="", namespaces=ATOM_NS)
            if not video_id:
                continue
            title = entry.findtext("a:title", default="", namespaces=ATOM_NS)
            link_el = entry.find("a:link[@rel='alternate']", ATOM_NS)
            url = (
                link_el.get("href")
                if link_el is not None
                else f"https://www.youtube.com/watch?v={video_id}"
            )
            if "/shorts/" in url:
                continue  # 只通知一般影片；Shorts 的 alternate link 是 /shorts/<id> 而非 /watch?v=
            published = entry.findtext("a:published", default="", namespaces=ATOM_NS)
            items.append(
                {
                    "video_id": video_id,
                    "title": title,
                    "url": url,
                    "published": published,
                }
            )
        items.sort(key=lambda it: it["published"])  # 不信任 feed 原始順序，同 wuwanews 慣例
        return items

    # ── 去重存取（同 wuwanews._load_sent_news / _save_sent_news 模式）──
    def _load_sent_ids(self) -> Optional[list]:
        if not os.path.exists(YT_DATA_FILE):
            return None  # None = 檔案不存在 → 觸發 seed
        try:
            with open(YT_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("ytnotify.json 損毀，視為不存在重新 seed")
            return None

    def _save_sent_ids(self, ids: list):
        os.makedirs(os.path.dirname(YT_DATA_FILE), exist_ok=True)
        with open(YT_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(ids[-MAX_KEPT_IDS:], f, ensure_ascii=False, indent=2)

    # ── 核心邏輯 ──
    async def _check_new_videos(self) -> int:
        xml_text = await self._fetch_feed_xml()
        if xml_text is None:
            return 0
        items = self._parse_feed(xml_text)
        if not items:
            logger.warning("RSS 解析結果為空，可能是頻道 ID 錯誤或抓取異常，本輪略過")
            return 0

        sent_ids = self._load_sent_ids()
        if sent_ids is None:
            # 首次啟動：全量記錄但不推送，避免把最新約 15 部影片全部炸出來
            self._save_sent_ids([it["video_id"] for it in items])
            logger.info(f"首次啟動，已 seed {len(items)} 筆既有影片，不推送")
            return 0

        sent_set = set(sent_ids)
        new_items = [it for it in items if it["video_id"] not in sent_set][:MAX_PUSH_PER_RUN]

        pushed = 0
        for item in new_items:
            ok = await self._push_video(item)
            if ok:
                sent_ids.append(item["video_id"])
                self._save_sent_ids(sent_ids)  # 每則成功後立即存檔，失敗的下輪自動重試
                pushed += 1
                await asyncio.sleep(1)
        return pushed

    async def _scheduled_check(self):
        await self.bot.wait_until_ready()
        try:
            n = await self._check_new_videos()
            if n:
                logger.info(f"本輪推送 {n} 則新影片")
        except Exception as e:
            logger.error(f"排程檢查發生未預期錯誤：{e}")

    # ── 推播 ──
    def _build_content(self, item: dict) -> str:
        """只送標題與網址，讓 Discord 自己展開 YouTube 預覽(含播放器)。

        不用自訂 embed：送了 embed 之後 Discord 就不再自動展開連結，
        預覽會退化成靜態圖、且影片說明動輒上千字塞進去會洗版。
        """
        lines = []
        if YT_NOTIFY_ROLE_ID:
            lines.append(f"<@&{YT_NOTIFY_ROLE_ID}>")
        lines.append(f"**{item['title']}**")
        lines.append(item["url"])
        return "\n".join(lines)

    async def _push_video(self, item: dict) -> bool:
        try:
            channel: TargetChannel = await self.bot.fetch_channel(YT_NEWS_THREAD_ID)
        except (discord.NotFound, discord.Forbidden) as e:
            logger.error(f"無法取得推播頻道 {YT_NEWS_THREAD_ID}：{e}")
            return False
        try:
            await channel.send(
                content=self._build_content(item), allowed_mentions=ALLOWED_MENTIONS
            )
            logger.info(f"已推送新影片：{item['title']} ({item['video_id']})")
            return True
        except discord.Forbidden:
            logger.error(f"沒有頻道 {YT_NEWS_THREAD_ID} 的發言權限")
        except Exception as e:
            logger.error(f"推送影片 {item['video_id']} 失敗：{e}")
        return False

    # ── 手動觸發（比照 bdnews.py `!fetchnews` / ff14news.py `!ff14test` 慣例）──
    @commands.command(name="ytcheck")
    @commands.is_owner()
    async def ytcheck_command(self, ctx: commands.Context):
        await ctx.send("🔍 正在檢查 YouTube 新影片...")
        n = await self._check_new_videos()
        await ctx.send(f"✅ 檢查完成，推送了 {n} 則新影片" if n else "目前沒有新影片")


async def setup(bot: commands.Bot):
    await bot.add_cog(YTNotify(bot))
