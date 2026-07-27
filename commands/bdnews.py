import os
import json
import aiohttp
import asyncio
import discord
from bs4 import BeautifulSoup
from discord.ext import commands
from config import BDNEWS_DATA_FILE, BDUST_REMINDER_CHANNEL_ID, BDUST_REMIND_USERS_FILE
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger  # 新增：每週提醒使用
from utils.logger import get_logger

logger = get_logger("BDNews", channel="bdnews")
DATA_FILE = BDNEWS_DATA_FILE
BDNEWS_CHANNELS_FILE = "data/bdnews_channels.json"

current_lang = 'zh-tw'

class Bdust(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.channels_config = self._load_channels_config()

    def _load_channels_config(self):
        """載入頻道配置"""
        # 向後兼容：嘗試從環境變數讀取舊配置
        from config import BDUST_NEWS_THREAD_ID
        
        if not os.path.exists(BDNEWS_CHANNELS_FILE):
            default_config = {"channel_ids": []}
            # 如果有舊的環境變數配置，遷移它
            if BDUST_NEWS_THREAD_ID and BDUST_NEWS_THREAD_ID != 0:
                default_config["channel_ids"] = [BDUST_NEWS_THREAD_ID]
                logger.info(f"從環境變數遷移 BD2 頻道配置: {BDUST_NEWS_THREAD_ID}")
            os.makedirs(os.path.dirname(BDNEWS_CHANNELS_FILE), exist_ok=True)
            with open(BDNEWS_CHANNELS_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=4)
            return default_config
        
        try:
            with open(BDNEWS_CHANNELS_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 向後兼容：如果配置是舊格式（單一 ID），轉換為列表格式
                if isinstance(config, dict) and "channel_ids" not in config:
                    old_id = config.get("channel_id") or config.get("BDUST_NEWS_THREAD_ID", 0)
                    if old_id and old_id != 0:
                        config = {"channel_ids": [old_id]}
                    else:
                        config = {"channel_ids": []}
                    self._save_channels_config(config)
                # 如果配置為空且有環境變數，遷移它
                elif isinstance(config, dict) and not config.get("channel_ids") and BDUST_NEWS_THREAD_ID and BDUST_NEWS_THREAD_ID != 0:
                    config["channel_ids"] = [BDUST_NEWS_THREAD_ID]
                    logger.info(f"從環境變數遷移 BD2 頻道配置到現有檔案: {BDUST_NEWS_THREAD_ID}")
                    self._save_channels_config(config)
                return config
        except Exception as e:
            logger.error(f"載入 BD2 頻道配置失敗: {e}")
            # 如果有環境變數，使用它作為後備
            if BDUST_NEWS_THREAD_ID and BDUST_NEWS_THREAD_ID != 0:
                return {"channel_ids": [BDUST_NEWS_THREAD_ID]}
            return {"channel_ids": []}

    def _save_channels_config(self, config=None):
        """儲存頻道配置"""
        if config is None:
            config = self.channels_config
        os.makedirs(os.path.dirname(BDNEWS_CHANNELS_FILE), exist_ok=True)
        with open(BDNEWS_CHANNELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)

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
    async def test_notify(self, ctx, channel: discord.TextChannel = None):
        """測試發送通知到指定頻道（或當前頻道）。用法: !test_notify [頻道]"""
        test_content = """
        <h2>🧪 BD2 新聞推送測試</h2>
        <p>這是一條測試通知的內容，包含多段文字與一些特殊格式。</p>
        <p>測試圖片連結：<img src='https://example.com/test_image.png'></p>
        <p>如果您看到這則訊息，表示推送功能正常運作！</p>
        """
        target_channel = channel or ctx.channel
        await self.notify_news_to_channel(target_channel.id, test_content)
        await ctx.send(f"✅ 測試通知已發送到 {target_channel.mention}！")

    @commands.command(name="bdtest")
    async def test_push_all(self, ctx, channel: discord.TextChannel = None):
        """測試推送到所有配置頻道或指定頻道。用法: !bdtest [頻道]"""
        test_content = """
        <h2>🧪 BD2 新聞推送測試</h2>
        <p>這是一條測試通知的內容，測試多頻道推送功能。</p>
        <p>如果您看到這則訊息，表示推送功能正常運作！</p>
        """
        
        if channel:
            # 測試單一指定頻道
            try:
                target_channel = await self.bot.fetch_channel(channel.id) if hasattr(channel, 'id') else channel
                await self.notify_news_to_channel(target_channel.id, test_content)
                await ctx.send(f"✅ 測試訊息已發送到 {channel.mention}")
            except Exception as e:
                await ctx.send(f"❌ 測試發送失敗: {e}")
                logger.error(f"BD2 測試推送失敗: {e}")
        else:
            # 測試所有配置的頻道
            channel_ids = self.channels_config.get("channel_ids", [])
            if not channel_ids:
                await ctx.send("❌ 沒有配置任何推送頻道，請先使用 `!bdchannels add` 添加頻道")
                return
            
            success_count = 0
            failed_count = 0
            for channel_id in channel_ids:
                try:
                    await self.notify_news_to_channel(channel_id, test_content)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"BD2 測試推送失敗 (頻道 {channel_id}): {e}")
            
            await ctx.send(f"✅ 測試完成！成功: {success_count}，失敗: {failed_count}")

    async def _fetch_news_data(self):
        list_url = f"https://webapi.browndust2.com/api/notices?locale={current_lang}&page=0&limit=20"
        async with aiohttp.ClientSession() as session:
            async with session.get(list_url) as response:
                if response.status != 200:
                    logger.warning(f"BD2 API 呼叫失敗：HTTP {response.status}")
                    return
                data = await response.json()

            news_items = data.get('items', [])
            filtered_news = [
                item for item in news_items
                if item.get('category') in ['inspection', 'update']
            ]

            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    existing_news = json.load(f)
            else:
                existing_news = []

            existing_ids = {news['id'] for news in existing_news}
            new_news = [item for item in filtered_news if item['id'] not in existing_ids]

            if not new_news:
                logger.info("沒有新的符合條件的新聞需要處理。")
                return

            # 由舊到新推送，維持時間順序
            new_news.sort(key=lambda item: item.get('publishedAt') or '')

            processed = []
            for item in new_news:
                detail_url = f"https://webapi.browndust2.com/api/notices/{item['id']}?locale={current_lang}"
                async with session.get(detail_url) as detail_response:
                    if detail_response.status != 200:
                        # 不記入 DATA_FILE，下一輪排程會重試
                        logger.warning(f"BD2 公告詳情抓取失敗：HTTP {detail_response.status}（id={item['id']}）")
                        continue
                    detail = await detail_response.json()

                await self.process_latest_news(detail)
                processed.append({
                    'id': detail['id'],
                    'subject': detail.get('subject'),
                    'category': detail.get('category'),
                    'publishedAt': detail.get('publishedAt'),
                })

            if processed:
                with open(DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(existing_news + processed, f, ensure_ascii=False, indent=4)

    async def process_latest_news(self, news_item):
        subject = news_item.get('subject')
        category = news_item.get('category')
        published_at = news_item.get('publishedAt')
        logger.info(f"最新新聞：{subject} [{category}] 發布於 {published_at}")

        content = news_item.get('contentHtml') or '無內容'
        await self.notify_news(content)

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

    async def notify_news(self, content):
        """推送新聞到所有配置的頻道"""
        channel_ids = self.channels_config.get("channel_ids", [])
        if not channel_ids:
            logger.warning("BD2 沒有配置任何推送頻道，跳過通知")
            return

        success_count = 0
        for channel_id in channel_ids:
            try:
                await self.notify_news_to_channel(channel_id, content)
                success_count += 1
            except Exception as e:
                logger.error(f"發送 BD2 新聞到頻道 {channel_id} 失敗: {e}")
        
        if success_count > 0:
            logger.info(f"BD2 新聞已成功發送到 {success_count}/{len(channel_ids)} 個頻道")

    async def notify_news_to_channel(self, channel_id, content):
        """推送新聞到單一頻道"""
        content_parts = self.clean_html_and_extract_images(content)

        try:
            target_channel = await self.bot.fetch_channel(channel_id)
        except discord.NotFound:
            logger.warning(f"❌ 找不到指定頻道 ID: {channel_id}")
            return
        except discord.Forbidden:
            logger.warning(f"❌ 機器人無權限讀取頻道 ID: {channel_id}")
            return

        if not target_channel:
            logger.warning(f"❌ 無法取得頻道 {channel_id}")
            return

        try:
            for part in content_parts:
                if not part['content'].strip():
                    logger.debug("跳過空內容")
                    continue

                if part['type'] == 'text':
                    for sub_part in [part['content'][i:i + 2000] for i in range(0, len(part['content']), 2000)]:
                        await target_channel.send(sub_part.strip())
                elif part['type'] == 'image':
                    await target_channel.send(part['content'])

            logger.info(f"✅ 內容成功發送到頻道 {channel_id}")
        except discord.Forbidden:
            logger.warning(f"❌ 無權限在頻道 {channel_id} 中發送訊息")
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

    @commands.group(name="bdchannels", invoke_without_command=True)
    async def bdchannels(self, ctx):
        """BD2 新聞推送頻道管理"""
        await ctx.send_help(ctx.command)

    async def _resolve_channel(self, ctx, channel_input):
        """解析頻道參數，支援同伺服器頻道和跨伺服器頻道 ID"""
        if channel_input is None:
            # 不提供參數，使用當前頻道
            return ctx.channel
        
        if isinstance(channel_input, discord.TextChannel):
            # 提供了頻道物件（同伺服器）
            return channel_input
        
        # 嘗試作為頻道 ID（跨伺服器）
        try:
            channel_id = int(str(channel_input))
            channel = await self.bot.fetch_channel(channel_id)
            return channel
        except (ValueError, discord.NotFound, discord.Forbidden):
            # 如果解析失敗，嘗試作為同伺服器的頻道名稱或提及
            try:
                converter = commands.TextChannelConverter()
                return await converter.convert(ctx, str(channel_input))
            except:
                raise commands.BadArgument(f"無法解析頻道：{channel_input}")

    @bdchannels.command(name="add")
    async def add_channel(self, ctx, channel_input=None):
        """新增推送頻道。用法: !bdchannels add [頻道/頻道ID]"""
        try:
            target_channel = await self._resolve_channel(ctx, channel_input)
        except Exception as e:
            await ctx.send(f"❌ 無法解析頻道：{e}")
            return
        
        channel_ids = self.channels_config.get("channel_ids", [])
        
        if target_channel.id in channel_ids:
            channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
            await ctx.send(f"ℹ️ {channel_mention} 已經在推送列表中了。")
            return
        
        channel_ids.append(target_channel.id)
        self.channels_config["channel_ids"] = channel_ids
        self._save_channels_config()
        channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
        await ctx.send(f"✅ 已將 {channel_mention} 加入 BD2 新聞推送列表。")

    @bdchannels.command(name="remove")
    async def remove_channel(self, ctx, channel_input=None):
        """移除推送頻道。用法: !bdchannels remove [頻道/頻道ID]"""
        try:
            target_channel = await self._resolve_channel(ctx, channel_input)
        except Exception as e:
            await ctx.send(f"❌ 無法解析頻道：{e}")
            return
        
        channel_ids = self.channels_config.get("channel_ids", [])
        
        if target_channel.id not in channel_ids:
            channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
            await ctx.send(f"ℹ️ {channel_mention} 不在推送列表中。")
            return
        
        channel_ids.remove(target_channel.id)
        self.channels_config["channel_ids"] = channel_ids
        self._save_channels_config()
        channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
        await ctx.send(f"✅ 已從 BD2 新聞推送列表中移除 {channel_mention}。")

    @bdchannels.command(name="list")
    async def list_channels(self, ctx):
        """列出所有推送頻道"""
        channel_ids = self.channels_config.get("channel_ids", [])
        
        if not channel_ids:
            await ctx.send("📭 目前沒有配置任何 BD2 新聞推送頻道。")
            return
        
        embed = discord.Embed(title="BD2 新聞推送頻道列表", color=discord.Color.blue())
        channels = [f"<#{cid}>" for cid in channel_ids]
        embed.description = "\n".join(channels) if channels else "無"
        await ctx.send(embed=embed)
    
async def setup(bot):
    await bot.add_cog(Bdust(bot))
