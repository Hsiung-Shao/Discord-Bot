import discord 
from discord.ext import commands
import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
from config import (
    VALORANT_BASE_URL,
    VALORANT_THREAD_ID,
    LOL_BASE_URL,
    LOL_THREAD_ID,
    RIOT_DATA_FILE,
)

logger = logging.getLogger("riotnews")

class RiotNews(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(self.fetch_and_push_news, "cron", hour=9, minute=0)
        self.scheduler.add_job(self.fetch_and_push_news, "cron", hour=20, minute=0)
        self.scheduler.start()

    @commands.command(name="riotnewsurl")
    async def test_specific_news(self, ctx, url: str):
        logger.info(f"手動測試抓取新聞：{url}")
        if "valorant" in url:
            content = self.fetch_valorant_patch_content(url)
            if isinstance(content, str):
                await self.send_long_message(ctx.channel, f"🔍 測試抓取內容：\n{url}\n\n{content}")
            else:
                await self.send_content_blocks(ctx.channel, f"🔍 測試抓取內容：\n{url}\n\n", content)
        else:
            content = self.fetch_lol_patch_content(url)
            if isinstance(content, str):
                await self.send_long_message(ctx.channel, f"🔍 測試抓取內容：\n{url}\n\n{content}")
            else:
                await self.send_content_blocks(ctx.channel, f"🔍 測試抓取內容：\n{url}\n\n", content)

    @commands.command(name="riotnews")
    async def manual_trigger_news(self, ctx):
        logger.info("手動觸發新聞推播")
        await self.fetch_and_push_news()

    def fetch_valorant_patch_links(self, limit=4):
        try:
            url = f"{VALORANT_BASE_URL}/zh-tw/news/game-updates/"
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(url, headers=headers)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            container = soup.find("div", class_="sc-362cdf8e-0 hSAVYW")
            if not container:
                logger.warning("找不到 Valorant 文章容器")
                return []

            links = container.find_all("a", href=True)
            result = []
            for a in links:
                href = a["href"]
                full_url = VALORANT_BASE_URL + href
                title = a.get_text(strip=True)
                if title and "/zh-tw/news/game-updates/" in href:
                    result.append({"title": title, "url": full_url})
                if len(result) >= limit:
                    break
            return result
        except Exception as e:
            logger.exception("抓取 Valorant 連結時發生錯誤")
            return []

    def fetch_lol_patch_links(self, limit=4):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            lol_patch_url = f"{LOL_BASE_URL}/zh-tw/news/tags/patch-notes/"
            res = requests.get(lol_patch_url, headers=headers)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            articles = soup.select("a[href*='/zh-tw/news/game-updates/']")

            result = []
            for a in articles:
                href = a["href"]
                full_url = LOL_BASE_URL + href if href.startswith("/") else href
                title = a.get_text(strip=True)
                if title and href:
                    result.append({"title": title, "url": full_url})
                if len(result) >= limit:
                    break
            return result
        except Exception as e:
            logger.exception("抓取 LoL 連結時發生錯誤")
            return []

    def dedup_lines(self, text_lines):
        seen = set()
        result = []
        for line in text_lines:
            if line not in seen:
                seen.add(line)
                result.append(line)
        return result

    def fetch_valorant_patch_content(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(url, headers=headers)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            blocks = soup.find_all("div", class_="sc-4225abdc-0 lnNUuw")
            if len(blocks) < 2:
                return "❌ 找不到主要內容區塊"

            content_blocks = [blocks[1]]
            if len(blocks) > 2:
                content_blocks.append(blocks[2])

            result = []
            for content_div in content_blocks:
                for el in content_div.find_all(["p", "li", "h1", "h2", "h3", "ul"]):
                    if el.name in ["h1", "h2", "h3"]:
                        level = {"h1": "#", "h2": "##", "h3": "###"}[el.name]
                        text = el.get_text(strip=True)
                        if text:
                            result.append(f"{level} {text}")
                    elif el.name == "ul":
                        for li in el.find_all("li"):
                            text = li.get_text(strip=True)
                            if text:
                                result.append(f"- {text}")
                    else:
                        text = el.get_text()
                        if text:
                            for tag, markdown in [("strong", "**"), ("b", "**"), ("em", "*"), ("i", "*")]:
                                for t in el.find_all(tag):
                                    t_text = t.get_text()
                                    text = text.replace(t_text, f"{markdown}{t_text}{markdown}")
                            result.append(text.strip("\n"))

            unique_lines = self.dedup_lines(result)
            return "\n".join(unique_lines)
        except Exception as e:
            logger.exception(f"抓取 VALORANT 文章內容失敗: {url}")
            return "❌ 抓取內容失敗"


    def fetch_lol_patch_content(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(url, headers=headers)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            blocks = soup.find_all("div", class_="sc-4225abdc-0 lnNUuw")
            if len(blocks) < 2:
                return "❌ 找不到第 2 個 rich-text-html 區塊"

            wrapper = blocks[1]
            container = wrapper.find(id="patch-notes-container")
            if not container:
                return "❌ 找不到 patch-notes-container 區塊"

            result = []
            for el in container.find_all(recursive=True):
                if el.name in ["h1", "h2", "h3"]:
                    level = {"h1": "#", "h2": "##", "h3": "###"}[el.name]
                    text = el.get_text(strip=True)
                    if text:
                        result.append({"type": "text", "content": f"{level} {text}"})
                elif el.name == "ul":
                    for li in el.find_all("li"):
                        text = li.get_text(strip=True)
                        if text:
                            result.append({"type": "text", "content": f"- {text}"})
                elif el.name == "blockquote":
                    text = el.get_text(strip=True)
                    if text:
                        result.append({"type": "text", "content": f"> {text}"})
                elif el.name == "img" and el.has_attr("src"):
                    result.append({"type": "image", "content": el["src"]})
                elif el.name in ["p", "header"]:
                    text = el.get_text()
                    if text:
                        for tag, md in [("strong", "**"), ("b", "**"), ("em", "*"), ("i", "*")]:
                            for t in el.find_all(tag):
                                t_text = t.get_text()
                                text = text.replace(t_text, f"{md}{t_text}{md}")
                        result.append({"type": "text", "content": text.strip("\n")})

            unique = []
            seen = set()
            for block in result:
                if block["type"] == "text" and block["content"] in seen:
                    continue
                if block["type"] == "text":
                    seen.add(block["content"])
                unique.append(block)
            return unique
        except Exception as e:
            logger.exception(f"抓取 LoL 文章內容失敗: {url}")
            return "❌ 抓取內容失敗"

    async def fetch_and_push_news(self, target_channel=None):
        logger.info("執行排程：更新並推送 Riot 新聞")
        news_items = self.fetch_and_update_news()
        if not news_items:
            logger.info("沒有新的更新項目")
            return

        for item in news_items:
            if "valorant" in item["url"]:
                channel = target_channel or self.bot.get_channel(VALORANT_THREAD_ID)
                content = item["content"]
                if isinstance(content, str):
                    await self.send_long_message(channel, f"【{item['title']}】\n{item['url']}\n\n{content}")
            else:
                channel = target_channel or self.bot.get_channel(LOL_THREAD_ID)
                content = self.fetch_lol_patch_content(item["url"])
                if isinstance(content, str):
                    await self.send_long_message(channel, f"【{item['title']}】\n{item['url']}\n\n{content}")
                else:
                    await self.send_content_blocks(channel, f"【{item['title']}】\n{item['url']}\n\n", content)

    async def send_content_blocks(self, channel, header, blocks):
        buffer = header
        for block in blocks:
            if block.get("type") == "image":
                if buffer.strip():
                    await self.send_long_message(channel, buffer)
                    buffer = ""
                embed = discord.Embed()
                embed.set_image(url=block["content"])
                await channel.send(embed=embed)
            else:
                buffer += block["content"] + "\n"
        if buffer.strip():
            await self.send_long_message(channel, buffer)

    async def send_long_message(self, channel, text, limit=1900):
        lines = text.split('\n')
        buffer = ''
        for line in lines:
            if len(buffer) + len(line) + 1 > limit:
                await channel.send(buffer)
                buffer = ''
            buffer += line + '\n'
        if buffer.strip():
            await channel.send(buffer)

    def fetch_and_update_news(self):
        valorant_links = self.fetch_valorant_patch_links(limit=4)
        lol_links = self.fetch_lol_patch_links(limit=4)

        new_data = self.load_news()
        new_data.setdefault("valorant", [])
        new_data.setdefault("lol", [])

        updated = []

        existing_valorant = {item["url"] for item in new_data["valorant"]}
        for item in valorant_links:
            if item["url"] in existing_valorant:
                continue
            content = self.fetch_valorant_patch_content(item["url"])
            entry = {
                "title": item["title"],
                "url": item["url"],
                "content": content,
                "fetched_at": datetime.now().isoformat()
            }
            new_data["valorant"].insert(0, entry)
            updated.append(entry)

        existing_lol = {item["url"] for item in new_data["lol"]}
        for item in lol_links:
            if item["url"] in existing_lol:
                continue
            entry = {
                "title": item["title"],
                "url": item["url"],
                "fetched_at": datetime.now().isoformat()
            }
            new_data["lol"].insert(0, entry)
            updated.append(entry)

        new_data["valorant"] = new_data["valorant"][:4]
        new_data["lol"] = new_data["lol"][:4]

        os.makedirs(os.path.dirname(RIOT_DATA_FILE), exist_ok=True)
        with open(RIOT_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)

        return updated

    def load_news(self):
        if not os.path.exists(RIOT_DATA_FILE):
            return {}
        with open(RIOT_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)


async def setup(bot):
    await bot.add_cog(RiotNews(bot))
