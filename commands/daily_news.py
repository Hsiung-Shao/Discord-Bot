import json
import os
from datetime import date, datetime

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
import logging
from utils.logger import get_logger

logger = get_logger("DailyNews", level=logging.WARNING)

NEWS_FILE_PATH = "data/daily_news.json"

# 寫入 daily_news.json 的「已推送」標記欄位,值為 "YYYY/MM/DD HH:MM:SS"
# 缺欄位或開頭日期不是今天,皆視為未推送。Claude 隔天覆寫整檔時自然消失。
PUSHED_AT_KEY = "bot_pushed_at"

# 排程觸發時間 (Asia/Taipei),多時段重試避免 Claude 排程延遲
CRON_SCHEDULES = [(10, 0), (10, 30), (11, 0)]

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


def _write_json(data: dict) -> None:
    """將 JSON 寫回 daily_news.json,保留 Claude 端的所有欄位。"""
    with open(NEWS_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class DailyNewsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()

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

            pushed_at = data.get(PUSHED_AT_KEY, "")
            if isinstance(pushed_at, str) and pushed_at.startswith(today_str):
                logger.info(f"今日 ({today_str}) 已推送 ({pushed_at}),跳過本次觸發")
                return

            logger.info(f"偵測到 {today_str} 的新聞,開始發送...")

            success_count = 0
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
                    success_count += 1
                except Exception as e:
                    logger.error(f"❌ {label} 發送失敗: {e}")

            if success_count == 0:
                logger.warning("本次無任何類別成功發送,不寫入 bot_pushed_at,下次觸發會重試")
                return

            now_str = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
            data[PUSHED_AT_KEY] = now_str
            try:
                _write_json(data)
                logger.info(f"🎉 今日 ({today_str}) 推送完畢 ({success_count} 類成功),已寫入 {PUSHED_AT_KEY}={now_str}")
            except Exception as e:
                logger.error(
                    f"⚠️ 寫入 {PUSHED_AT_KEY} 失敗: {e}。已成功發送 {success_count} 類,"
                    f"下次觸發可能會重發,請手動處理或忽略"
                )

        except Exception as e:
            logger.error(f"每日新聞檢查發送流程發生未預期錯誤: {e}")

    @commands.command(name="force_daily_news")
    @commands.is_owner()
    async def force_daily_news(self, ctx: commands.Context):
        """手動清空今日的推送標記並立即重新檢查 (owner-only)。"""
        cleared = False
        if os.path.exists(NEWS_FILE_PATH):
            try:
                with open(NEWS_FILE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get(PUSHED_AT_KEY):
                    data[PUSHED_AT_KEY] = ""
                    _write_json(data)
                    cleared = True
            except Exception as e:
                await ctx.send(f"⚠️ 清空推送標記時發生錯誤: {e},但仍會嘗試重新檢查")
                logger.error(f"force_daily_news 清空 {PUSHED_AT_KEY} 失敗: {e}")

        msg = "✅ 已清空推送標記" if cleared else "ℹ️ 推送標記本就為空,無需清空"
        await ctx.send(f"{msg},立即重新檢查...")
        await self._check_and_send_news()


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyNewsCog(bot))
