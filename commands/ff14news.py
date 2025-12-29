import os
import json
import aiohttp
import asyncio
import discord
from bs4 import BeautifulSoup, Tag, NavigableString
from discord.ext import commands
from discord.ui import View, Button
from config import FF14_DATA_FILE
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from utils.logger import get_logger
from urllib.parse import urljoin
logger = get_logger("FF14News")

FF14_CHANNELS_FILE = "data/ff14news_channels.json"

class FF14News(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.base_url = "https://www.ffxiv.com.tw/web/news/"
        self.news_list_urls = [
            "https://www.ffxiv.com.tw/web/news/news_list.aspx?category=1",
            "https://www.ffxiv.com.tw/web/news/news_list.aspx?category=2",
            "https://www.ffxiv.com.tw/web/news/news_list.aspx?category=3"
        ]
        self.channels_config = self._load_channels_config()

    def _load_channels_config(self):
        """載入頻道配置"""
        # 向後兼容：嘗試從環境變數讀取舊配置
        from config import FF14_NEWS_THREAD_ID
        
        if not os.path.exists(FF14_CHANNELS_FILE):
            default_config = {"channel_ids": []}
            # 如果有舊的環境變數配置，遷移它
            if FF14_NEWS_THREAD_ID and FF14_NEWS_THREAD_ID != 0:
                default_config["channel_ids"] = [FF14_NEWS_THREAD_ID]
                logger.info(f"從環境變數遷移 FF14 頻道配置: {FF14_NEWS_THREAD_ID}")
            os.makedirs(os.path.dirname(FF14_CHANNELS_FILE), exist_ok=True)
            with open(FF14_CHANNELS_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=4)
            return default_config
        
        try:
            with open(FF14_CHANNELS_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 向後兼容：如果配置是舊格式（單一 ID），轉換為列表格式
                if isinstance(config, dict) and "channel_ids" not in config:
                    old_id = config.get("channel_id") or config.get("FF14_NEWS_THREAD_ID", 0)
                    if old_id and old_id != 0:
                        config = {"channel_ids": [old_id]}
                    else:
                        config = {"channel_ids": []}
                    self._save_channels_config(config)
                # 如果配置為空且有環境變數，遷移它
                elif isinstance(config, dict) and not config.get("channel_ids") and FF14_NEWS_THREAD_ID and FF14_NEWS_THREAD_ID != 0:
                    config["channel_ids"] = [FF14_NEWS_THREAD_ID]
                    logger.info(f"從環境變數遷移 FF14 頻道配置到現有檔案: {FF14_NEWS_THREAD_ID}")
                    self._save_channels_config(config)
                return config
        except Exception as e:
            logger.error(f"載入 FF14 頻道配置失敗: {e}")
            # 如果有環境變數，使用它作為後備
            if FF14_NEWS_THREAD_ID and FF14_NEWS_THREAD_ID != 0:
                return {"channel_ids": [FF14_NEWS_THREAD_ID]}
            return {"channel_ids": []}

    def _save_channels_config(self, config=None):
        """儲存頻道配置"""
        if config is None:
            config = self.channels_config
        os.makedirs(os.path.dirname(FF14_CHANNELS_FILE), exist_ok=True)
        with open(FF14_CHANNELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)

    async def cog_load(self):
        self._start_scheduler()

    def _start_scheduler(self):
        self.scheduler.add_job(self._fetch_news_task, IntervalTrigger(hours=1))
        self.scheduler.start()
        logger.info("FF14 News scheduler started.")

    def _load_sent_news(self):
        if not os.path.exists(FF14_DATA_FILE):
            return []
        try:
            with open(FF14_DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []

    def _save_sent_news(self, news_ids):
        os.makedirs(os.path.dirname(FF14_DATA_FILE), exist_ok=True)
        with open(FF14_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(news_ids, f, ensure_ascii=False, indent=4)

    async def _fetch_news_task(self):
        logger.info("Starting FF14 news fetch task...")
        sent_news = self._load_sent_news()
        new_items = []

        for url in self.news_list_urls:
            try:
                items = await self._fetch_news_list(url)
                for item in items:
                    if item['id'] not in sent_news:
                        new_items.append(item)
            except Exception as e:
                logger.error(f"Error fetching news list from {url}: {e}")

        # Reverse to send oldest first
        new_items.reverse()
        
        # Deduplicate based on ID
        unique_new_items = []
        seen_ids = set()
        for item in new_items:
            if item['id'] not in seen_ids and item['id'] not in sent_news:
                unique_new_items.append(item)
                seen_ids.add(item['id'])

        for item in unique_new_items:
            try:
                await self.notify_news(item)
                sent_news.append(item['id'])
                self._save_sent_news(sent_news)
                await asyncio.sleep(1) 
            except Exception as e:
                logger.error(f"Error processing news item {item['id']}: {e}")

    async def _fetch_news_list(self, url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch {url}: {response.status}")
                    return []
                html = await response.text()
        
        soup = BeautifulSoup(html, 'html.parser')
        news_items = []
        
        # Target: div class="list news_list" -> div class="item"
        list_div = soup.find('div', class_='list news_list')
        if not list_div:
            return []

        # Limit to top 5 items as requested
        items = list_div.find_all('div', class_='item', limit=10)
        
        for item_div in items:
            second_block = item_div.find('div', class_='second_block')
            if not second_block:
                continue
                
            title_div = second_block.find('div', class_='title new') or second_block.find('div', class_='title')
            date_div = second_block.find('div', class_='publish_date')
            
            if title_div and title_div.find('a'):
                a_tag = title_div.find('a')
                href = a_tag.get('href')
                title = a_tag.get_text(strip=True)
                date_str = date_div.get_text(strip=True) if date_div else "Unknown Date"
                
                if 'id=' in href:
                    news_id = href.split('id=')[1].split('&')[0]
                    full_url = f"https://www.ffxiv.com.tw/web/news/{href}"
                    news_items.append({
                        'id': news_id,
                        'title': title,
                        'url': full_url,
                        'date': date_str
                    })
        
        return news_items

    async def _fetch_news_detail(self, url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                html = await response.text()
        
        soup = BeautifulSoup(html, 'html.parser')
        article_div = soup.find('div', class_='article')
        
        if not article_div:
            return None
            
        # Return the raw HTML string of the article div
        return str(article_div)

    def clean_html_and_extract_images(self, raw_html):
        soup = BeautifulSoup(raw_html, "html.parser")
        content_parts = []

        # 1) 先收集文字區塊（以段落、標題或 div p 為單位）
        # 可依需要調整 tag 列表
        text_tags = soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'li'])
        seen_texts = set()
        for tag in text_tags:
            # 避免把整個 article 的文字重複加入（只取非空且去重）
            text = tag.get_text(separator="\n", strip=True)
            if text and text not in seen_texts:
                content_parts.append({"type": "text", "content": text})
                seen_texts.add(text)

        # 2) 收集圖片（只遍歷 <img>，避免 descendants 導致的型別錯誤）
        for img in soup.find_all('img'):
            if not isinstance(img, Tag):
                continue
            # 優先 data-src（lazy load）再 fallback src
            src = img.get("data-src") or img.get("src") or img.get("data-original")
            if not src:
                continue
            src = src.strip()
            # 用 urljoin 處理相對路徑或開頭為 //
            full_url = urljoin("https://www.ffxiv.com.tw", src)
            content_parts.append({"type": "image", "content": full_url})

        return content_parts

    async def send_news_message(self, channel, item):
        embed = discord.Embed(
            title=item['title'],
            url=item['url'],
            description=f"發布日期: {item['date']}",
            color=0x0099ff
        )
        
        view = View()
        button = Button(label="查看詳情", style=discord.ButtonStyle.primary, custom_id=f"ff14_news:{item['id']}")
        view.add_item(button)
        
        try:
            await channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send news notification: {e}")

    async def notify_news(self, item):
        """推送新聞到所有配置的頻道（支援跨伺服器）"""
        channel_ids = self.channels_config.get("channel_ids", [])
        if not channel_ids:
            logger.warning("FF14 沒有配置任何推送頻道，跳過通知")
            return

        success_count = 0
        for channel_id in channel_ids:
            try:
                # 使用 fetch_channel 以支援跨伺服器
                channel = await self.bot.fetch_channel(channel_id)
                if channel:
                    await self.send_news_message(channel, item)
                    success_count += 1
                    logger.info(f"FF14 新聞已發送到頻道 {channel_id}")
            except discord.NotFound:
                logger.warning(f"找不到 FF14 頻道 {channel_id}")
            except discord.Forbidden:
                logger.warning(f"無權限存取 FF14 頻道 {channel_id}")
            except Exception as e:
                logger.error(f"發送 FF14 新聞到頻道 {channel_id} 失敗: {e}")
        
        if success_count > 0:
            logger.info(f"FF14 新聞已成功發送到 {success_count}/{len(channel_ids)} 個頻道")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.data or 'custom_id' not in interaction.data:
            return

        custom_id = interaction.data['custom_id']
        if not custom_id.startswith('ff14_news:'):
            return

        # Acknowledge the interaction silently (ephemeral)
        await interaction.response.defer(ephemeral=True)

        try:
            news_id = custom_id.split(':')[1]
            url = f"https://www.ffxiv.com.tw/web/news/news_content.aspx?id={news_id}"
            
            raw_html = await self._fetch_news_detail(url)
            if not raw_html:
                await interaction.followup.send("無法獲取文章內容。", ephemeral=True)
                return

            try:
                content_parts = self.clean_html_and_extract_images(raw_html)
            except Exception as e:
                logger.error(f"clean_html_and_extract_images failed for {url}: {e}")
                content_parts = []
            
            # Send to the channel directly instead of replying to the interaction
            target_channel = interaction.channel
            if not target_channel:
                target_channel = self.bot.get_channel(interaction.channel_id)

            # Send title first
            await target_channel.send(f"**文章詳情**\n連結: {url}")

            for part in content_parts:
                if not part['content'].strip():
                    continue

                if part['type'] == 'text':
                    # Split text if > 2000 chars
                    text = part['content']
                    chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
                    for chunk in chunks:
                        await target_channel.send(chunk)
                elif part['type'] == 'image':
                    await target_channel.send(part['content'])

        except Exception as e:
            logger.error(f"Error handling interaction: {e}")
            await interaction.followup.send(f"發生錯誤: {e}", ephemeral=True)

    @commands.command(name="ff14test")
    async def ff14test(self, ctx):
        """Test FF14 news push with the latest news items (simulating a batch update)."""
        await ctx.send("Fetching latest FF14 news from all sources for test...")
        try:
            all_items = []
            for url in self.news_list_urls:
                items = await self._fetch_news_list(url)
                all_items.extend(items)
            
            if not all_items:
                await ctx.send("No news found.")
                return

            # Deduplicate based on ID
            unique_items = []
            seen_ids = set()
            for item in all_items:
                if item['id'] not in seen_ids:
                    unique_items.append(item)
                    seen_ids.add(item['id'])
            
            # Sort by ID (assuming higher ID is newer) or just take the first few since fetch returns newest first
            # Since we extended lists, the order might be mixed. Let's trust the fetch order for now or sort if needed.
            # Actually, _fetch_news_list returns newest first. 
            # But since we are combining multiple lists, we might want to sort.
            # However, for a simple test, taking the first 3 unique items found (which are likely the newest from the first few categories) is acceptable.
            # Better: Sort by ID descending (assuming numeric IDs or lexicographical order works for date)
            # The IDs look like 'LENEEYbBA', which might not be sortable by simple string comparison for date.
            # But usually the first items in the list are the newest.
            
            # Let's just take the top 3 from the unique list.
            top_items = unique_items[:3]
            
            # Reverse to send oldest of the batch first (simulating the loop in _fetch_news_task)
            top_items.reverse()

            await ctx.send(f"Found {len(unique_items)} unique items. Sending top {len(top_items)}...")

            for item in top_items:
                await self.send_news_message(ctx, item)
                await asyncio.sleep(1) # Small delay between messages
                    
        except Exception as e:
            await ctx.send(f"Test failed: {e}")
            logger.error(f"Test push failed: {e}")

    @commands.group(name="ff14channels", invoke_without_command=True)
    async def ff14channels(self, ctx):
        """FF14 新聞推送頻道管理"""
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

    @ff14channels.command(name="add")
    async def add_channel(self, ctx, channel_input=None):
        """新增推送頻道。用法: !ff14channels add [頻道/頻道ID]"""
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
        await ctx.send(f"✅ 已將 {channel_mention} 加入 FF14 新聞推送列表。")

    @ff14channels.command(name="remove")
    async def remove_channel(self, ctx, channel_input=None):
        """移除推送頻道。用法: !ff14channels remove [頻道/頻道ID]"""
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
        await ctx.send(f"✅ 已從 FF14 新聞推送列表中移除 {channel_mention}。")

    @ff14channels.command(name="list")
    async def list_channels(self, ctx):
        """列出所有推送頻道"""
        channel_ids = self.channels_config.get("channel_ids", [])
        
        if not channel_ids:
            await ctx.send("📭 目前沒有配置任何 FF14 新聞推送頻道。")
            return
        
        embed = discord.Embed(title="FF14 新聞推送頻道列表", color=discord.Color.blue())
        channels = [f"<#{cid}>" for cid in channel_ids]
        embed.description = "\n".join(channels) if channels else "無"
        await ctx.send(embed=embed)

    @ff14channels.command(name="test")
    async def test_push(self, ctx, channel: discord.TextChannel = None):
        """測試推送功能到指定頻道（或所有配置頻道）。用法: !ff14channels test [頻道]"""
        test_item = {
            'id': 'TEST',
            'title': '🧪 FF14 新聞推送測試',
            'url': 'https://www.ffxiv.com.tw/web/news/',
            'date': '測試日期'
        }
        
        if channel:
            # 測試單一指定頻道
            try:
                target_channel = await self.bot.fetch_channel(channel.id) if hasattr(channel, 'id') else channel
                await self.send_news_message(target_channel, test_item)
                await ctx.send(f"✅ 測試訊息已發送到 {channel.mention}")
            except Exception as e:
                await ctx.send(f"❌ 測試發送失敗: {e}")
                logger.error(f"FF14 測試推送失敗: {e}")
        else:
            # 測試所有配置的頻道
            channel_ids = self.channels_config.get("channel_ids", [])
            if not channel_ids:
                await ctx.send("❌ 沒有配置任何推送頻道，請先使用 `!ff14channels add` 添加頻道")
                return
            
            success_count = 0
            failed_count = 0
            for channel_id in channel_ids:
                try:
                    target_channel = await self.bot.fetch_channel(channel_id)
                    await self.send_news_message(target_channel, test_item)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"FF14 測試推送失敗 (頻道 {channel_id}): {e}")
            
            await ctx.send(f"✅ 測試完成！成功: {success_count}，失敗: {failed_count}")

async def setup(bot):
    await bot.add_cog(FF14News(bot))
