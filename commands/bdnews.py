import os
import json
import aiohttp
import asyncio
import discord
from bs4 import BeautifulSoup
from discord.ext import commands
from config import BDNEWS_DATA_FILE, BDUST_NEWS_THREAD_ID, BDUST_REMINDER_CHANNEL_ID, BDUST_REMIND_USERS_FILE
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger  # 新增：每週提醒使用
from utils.logger import get_logger

logger = get_logger("BDNews")
DATA_FILE = BDNEWS_DATA_FILE

lang_map = {
    'en-us': 'en',
    'zh-tw': 'tw',
    'zh-cn': 'cn',
    'ja-jp': 'jp',
    'ko-kr': 'kr',
}
current_lang = 'zh-tw'

class Bdust(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()

    async def cog_load(self):
        """在 Cog 載入後啟動排程器（延遲到 bot 啟動完成）"""
        self._start_scheduler()

    def _start_scheduler(self):
        self.scheduler.add_job(self._fetch_news_data, IntervalTrigger(hours=1))
        self.scheduler.add_job(self._weekly_reminder, CronTrigger(day_of_week='sun', hour=22, minute=30, timezone='Asia/Taipei'))
        self.scheduler.start()
        logger.info("BDNews 排程器已啟動，每小時執行一次。")

    @commands.command(name="fetchnews")
    async def fetch_news_command(self, ctx):
        """手動觸發新聞抓取"""
        await ctx.send("正在抓取最新新聞數據，請稍候...")
        try:
            await self._fetch_news_data()
            await ctx.send("✅ 新聞抓取完成！")
        except Exception as e:
            await ctx.send(f"❌ 抓取過程中發生錯誤：{e}")
            logger.warning(f"手動抓取新聞失敗：{e}")

    @commands.command(name="test_notify")
    async def test_notify(self, ctx, thread_id: int):
        """測試發送通知到指定討論串"""
        test_content = """
        這是一條測試通知的內容，包含多段文字與一些特殊格式。
        測試圖片連結：<img src='https://example.com/test_image.png'>
        """
        await self.notify_news(thread_id, test_content)
        await ctx.send("✅ 測試通知已發送！")

    async def _fetch_news_data(self):
        lang_code = lang_map[current_lang]
        url = f"https://www.browndust2.com/api/newsData_{lang_code}.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    news_items = data.get('data', [])
                    filtered_news = [
                        item for item in news_items
                        if item['attributes']['tag'] in ['dev_note', 'maintenance']
                    ]

                    if os.path.exists(DATA_FILE):
                        with open(DATA_FILE, 'r', encoding='utf-8') as f:
                            existing_news = json.load(f)
                    else:
                        existing_news = []

                    existing_ids = {news['id'] for news in existing_news}
                    new_news = [item for item in filtered_news if item['id'] not in existing_ids]

                    if new_news:
                        updated_news = existing_news + new_news
                        with open(DATA_FILE, 'w', encoding='utf-8') as f:
                            json.dump(updated_news, f, ensure_ascii=False, indent=4)

                        for news_item in new_news:
                            await self.process_latest_news(news_item)
                    else:
                        logger.info("沒有新的符合條件的新聞需要處理。")
                else:
                    logger.warning(f"BD2 API 呼叫失敗：HTTP {response.status}")

    async def process_latest_news(self, news_item):
        attributes = news_item['attributes']
        subject = attributes['subject']
        tag = attributes['tag']
        published_at = attributes['publishedAt']
        logger.info(f"最新新聞：{subject} [{tag}] 發布於 {published_at}")

        content = attributes.get('NewContent', '無內容')
        await self.notify_news(BDUST_NEWS_THREAD_ID, content)

    def clean_html_and_extract_images(self, raw_html):
        soup = BeautifulSoup(raw_html, "html.parser")
        content_parts = []

        for element in soup.descendants:
            if element.name == "img":
                image_url = element.get("src")
                if image_url:
                    content_parts.append({"type": "image", "content": image_url})
            elif element.name is None and isinstance(element, str):
                text_content = element.strip()
                if text_content:
                    if content_parts and content_parts[-1]["type"] == "text":
                        content_parts[-1]["content"] += f"\n{text_content}"
                    else:
                        content_parts.append({"type": "text", "content": text_content})

        return content_parts

    async def notify_news(self, thread_id, content):
        content_parts = self.clean_html_and_extract_images(content)

        try:
            target_thread = await self.bot.fetch_channel(thread_id)
        except discord.NotFound:
            logger.warning(f"❌ 找不到指定貼文 ID: {thread_id}")
            return
        except discord.Forbidden:
            logger.warning(f"❌ 機器人無權限讀取貼文 ID: {thread_id}")
            return

        if not isinstance(target_thread, discord.Thread):
            logger.warning(f"❌ ID {thread_id} 並不是有效的論壇貼文")
            return

        try:
            for part in content_parts:
                if not part['content'].strip():
                    logger.debug("跳過空內容")
                    continue

                if part['type'] == 'text':
                    for sub_part in [part['content'][i:i + 2000] for i in range(0, len(part['content']), 2000)]:
                        await target_thread.send(sub_part.strip())
                elif part['type'] == 'image':
                    await target_thread.send(part['content'])

            logger.info(f"✅ 內容成功發送到論壇貼文 {thread_id}")
        except discord.Forbidden:
            logger.warning(f"❌ 無權限在 {target_thread.name} 中發送訊息")
        except discord.HTTPException as e:
            logger.warning(f"❌ 發送訊息時發生 HTTP 錯誤：{e}")
        except Exception as e:
            logger.warning(f"❌ 發送訊息時發生未知錯誤：{e}")


    # ==========================
    # 🆕 每週提醒系統 - 起始
    # ==========================

    def _load_reminders(self):
        if not os.path.exists(BDUST_REMIND_USERS_FILE):
            return []
        with open(BDUST_REMIND_USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_reminders(self, user_ids):
        with open(BDUST_REMIND_USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_ids, f)

    @commands.command(name="remindme")
    async def remind_me(self, ctx):
        user_ids = self._load_reminders()
        if ctx.author.id not in user_ids:
            user_ids.append(ctx.author.id)
            self._save_reminders(user_ids)
            await ctx.send("✅ 你已加入每週提醒名單。")
        else:
            await ctx.send("ℹ️ 你已在提醒名單中。")

    @commands.command(name="unremindme")
    async def unremind_me(self, ctx):
        user_ids = self._load_reminders()
        if ctx.author.id in user_ids:
            user_ids.remove(ctx.author.id)
            self._save_reminders(user_ids)
            await ctx.send("✅ 你已退出每週提醒名單。")
        else:
            await ctx.send("ℹ️ 你目前不在提醒名單中。")

    @commands.command(name="listreminders")
    async def list_reminders(self, ctx):
        user_ids = self._load_reminders()
        mentions = [f"<@{uid}>" for uid in user_ids]
        if mentions:
            await ctx.send("📋 當前提醒名單:\n" + "\n".join(mentions))
        else:
            await ctx.send("⚠️ 無人報名。")

    async def _weekly_reminder(self):
        user_ids = self._load_reminders()
        if not user_ids or BDUST_REMINDER_CHANNEL_ID == 0:
            logger.info("提醒名單為空或頻道未設定，跳過每週提醒。")
            return

        try:
            channel = await self.bot.fetch_channel(BDUST_REMINDER_CHANNEL_ID)
            mentions = " ".join([f"<@{uid}>" for uid in user_ids])
            message = f"⏰ 本周PVP即將結算 {mentions}"
            await channel.send(message)
            logger.info("每週提醒已發送。")
        except Exception as e:
            logger.error(f"每週提醒發送失敗：{e}")
    
async def setup(bot):
    await bot.add_cog(Bdust(bot))
