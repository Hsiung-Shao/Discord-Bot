import os
import json
import aiohttp
import asyncio
import discord
from bs4 import BeautifulSoup
from discord.ext import commands
from discord.ui import View, Button
from config import FF14_DATA_FILE, FF14_NEWS_THREAD_ID
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from utils.logger import get_logger

logger = get_logger("FF14News")

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

        for element in soup.descendants:
            if element.name == "img":
                image_url = element.get("src")
                if image_url:
                    if not image_url.startswith('http'):
                        image_url = f"https://www.ffxiv.com.tw{image_url}"
                    content_parts.append({"type": "image", "content": image_url})
            elif element.name is None and isinstance(element, str):
                text_content = element.strip()
                if text_content:
                    if content_parts and content_parts[-1]["type"] == "text":
                        content_parts[-1]["content"] += f"\n{text_content}"
                    else:
                        content_parts.append({"type": "text", "content": text_content})
        
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
        if FF14_NEWS_THREAD_ID == 0:
            logger.warning("FF14_NEWS_THREAD_ID not set, skipping notification.")
            return

        channel = self.bot.get_channel(FF14_NEWS_THREAD_ID)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(FF14_NEWS_THREAD_ID)
            except Exception as e:
                logger.error(f"Could not fetch channel {FF14_NEWS_THREAD_ID}: {e}")
                return

        await self.send_news_message(channel, item)

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

            content_parts = self.clean_html_and_extract_images(raw_html)
            
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

async def setup(bot):
    await bot.add_cog(FF14News(bot))
