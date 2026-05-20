import json
import os
from datetime import date

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    DAILY_NEWS_TECH_CHANNEL_ID,
    DAILY_NEWS_AI_CHANNEL_ID,
    DAILY_NEWS_GITHUB_CHANNEL_ID,
    DAILY_NEWS_STOCK_CHANNEL_ID,
)
from utils.logger import get_logger

logger = get_logger("DailyNews", channel="daily_news")

NEWS_FILE_PATH = "data/daily_news.json"

# 排程觸發時間 (Asia/Taipei),多時段重試避免 Claude 排程延遲
CRON_SCHEDULES = [(9, 30), (10, 0), (10, 30)]

# (JSON key, channel ID, 顯示標籤)
CATEGORY_MAP = [
    ("tech",   DAILY_NEWS_TECH_CHANNEL_ID,   "📱 科技新聞"),
    ("ai",     DAILY_NEWS_AI_CHANNEL_ID,     "🤖 AI 新聞"),
    ("github", DAILY_NEWS_GITHUB_CHANNEL_ID, "⭐ GitHub 熱門"),
    ("stock",  DAILY_NEWS_STOCK_CHANNEL_ID,  "📈 股市新聞"),
]


async def send_long_message(channel: discord.abc.Messageable, content: str) -> None:
    """超過 Discord 2000 字限制時,以換行為界自動分段發送。"""
    while len(content) > 1900:
        split_at = content.rfind("\n", 0, 1900)
        if split_at == -1:
            split_at = 1900
        await channel.send(content[:split_at])
        content = content[split_at:].lstrip("\n")
    if content.strip():
        await channel.send(content)


class DailyNewsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.sent_date: str | None = None

    async def cog_load(self):
        self._start_scheduler()

    def cog_unload(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _start_scheduler(self):
        for hour, minute in CRON_SCHEDULES:
            self.scheduler.add_job(
                self._check_and_send_news,
                CronTrigger(hour=hour, minute=minute, timezone="Asia/Taipei"),
            )
        self.scheduler.start()
        slots = ", ".join(f"{h:02d}:{m:02d}" for h, m in CRON_SCHEDULES)
        logger.info(f"每日新聞排程已啟動,觸發時段 (Asia/Taipei): {slots}")

    async def _check_and_send_news(self):
        try:
            today_str = date.today().strftime("%Y/%m/%d")

            if self.sent_date == today_str:
                logger.info(f"今日 ({today_str}) 已發送過,跳過本次觸發")
                return

            if not os.path.exists(NEWS_FILE_PATH):
                logger.warning(f"{NEWS_FILE_PATH} 不存在,等待下次重試")
                return

            try:
                with open(NEWS_FILE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"{NEWS_FILE_PATH} JSON 格式錯誤,等待下次重試")
                return

            news_date = data.get("date", "")
            if news_date != today_str:
                logger.info(f"daily_news.json 日期 {news_date!r} 非今日 {today_str},跳過")
                return

            logger.info(f"偵測到 {today_str} 的新聞,開始發送...")

            for key, channel_id, label in CATEGORY_MAP:
                content = data.get(key, "")
                if not channel_id or not content:
                    logger.warning(f"⚠️ {label} 跳過 (無頻道ID或內容為空)")
                    continue

                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except discord.NotFound:
                    logger.error(f"❌ 找不到 {label} 頻道 {channel_id}")
                    continue
                except discord.Forbidden:
                    logger.error(f"❌ 無權限存取 {label} 頻道 {channel_id}")
                    continue
                except Exception as e:
                    logger.error(f"❌ 取得 {label} 頻道 {channel_id} 失敗: {e}")
                    continue

                try:
                    await send_long_message(channel, content)
                    logger.info(f"✅ {label} 發送完成")
                except Exception as e:
                    logger.error(f"❌ {label} 發送失敗: {e}")

            self.sent_date = today_str
            logger.info(f"🎉 今日 ({today_str}) 所有新聞發送完畢")

        except Exception as e:
            logger.error(f"每日新聞檢查發送流程發生未預期錯誤: {e}")

    @commands.command(name="force_daily_news")
    @commands.is_owner()
    async def force_daily_news(self, ctx: commands.Context):
        """手動重置今日發送狀態並立即重新檢查 (owner-only)。"""
        self.sent_date = None
        await ctx.send("✅ 已重置今日發送狀態,立即重新檢查...")
        await self._check_and_send_news()


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyNewsCog(bot))
