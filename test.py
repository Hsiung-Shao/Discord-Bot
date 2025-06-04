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
        self.scheduler.start()

    @commands.command(name="riotnewsurl")
    async def test_specific_news(self, ctx, url: str):
        logger.info(f"手動測試抓取新聞：{url}")
        if "valorant" in url:
            content_blocks = self.fetch_valorant_patch_content(url)
        else:
            content_blocks = self.fetch_lol_patch_content(url)
        if isinstance(content_blocks, str) and content_blocks.startswith("❌"):
            await ctx.send(content_blocks)
            return

        header = f"🔍 測試抓取內容：\n{url}\n\n"
        await self.send_content_blocks(LOL_THREAD_ID, header, content_blocks)

    async def send_content_blocks(self, channel, header, blocks):
        await channel.send(header)
        for block in blocks:
            if block.get("type") == "image":
                embed = discord.Embed()
                embed.set_image(url=block["content"])
                await channel.send(embed=embed)
            else:
                await self.send_long_message(channel, block["content"])

    def fetch_valorant_patch_content(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(url, headers=headers)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            blocks = soup.find_all("div", class_="sc-4225abdc-0 lnNUuw")
            if len(blocks) < 2:
                return "❌ 找不到第 2 區塊"

            content_div = blocks[1]
            result = []

            for el in content_div.find_all(["p", "li", "h1", "h2", "h3", "ul", "img"]):
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
                elif el.name == "img" and el.has_attr("src"):
                    result.append({"type": "image", "content": el["src"]})
                else:
                    text = el.get_text()
                    if text:
                        for tag, markdown in [("strong", "**"), ("b", "**"), ("em", "*"), ("i", "*")]:
                            for t in el.find_all(tag):
                                t_text = t.get_text()
                                text = text.replace(t_text, f"{markdown}{t_text}{markdown}")
                        result.append({"type": "text", "content": text.strip("\n")})

            unique = []
            seen = set()
            for block in result:
                if block["type"] == "text":
                    if block["content"] in seen:
                        continue
                    seen.add(block["content"])
                unique.append(block)

            return unique
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
                if block["type"] == "text":
                    if block["content"] in seen:
                        continue
                    seen.add(block["content"])
                unique.append(block)

            return unique
        except Exception as e:
            logger.exception(f"抓取 LoL 文章內容失敗: {url}")
            return "❌ 抓取內容失敗"

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

    # 其餘邏輯如 fetch_and_update_news, fetch_and_push_news 等可照原邏輯使用 blocks 送出

async def setup(bot):
    await bot.add_cog(RiotNews(bot))
