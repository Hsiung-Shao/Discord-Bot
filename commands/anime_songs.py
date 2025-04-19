import discord
from discord.ext import commands
from bs4 import BeautifulSoup
import requests
import json
import os
from collections import defaultdict

from config import ANIME_SONG_DATA_FILE

class AnimeSongFetcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        os.makedirs(os.path.dirname(ANIME_SONG_DATA_FILE), exist_ok=True)

    def parse_acgsecrets_season(self, url: str, season_key: str) -> dict:
        try:
            res = requests.get(url, timeout=10)
            res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, 'html.parser')
            anime_data = defaultdict(lambda: {
                "title_localized": "",
                "title_original": "",
                "op": [],
                "ed": []
            })

            anime_blocks = soup.select('div.spannable-main-content')
            if not anime_blocks:
                return {}

            for block in anime_blocks:
                data_tag = block.select_one('.anime_data')
                if not data_tag:
                    continue

                anime_key = data_tag.get_text(strip=True)

                title_info = block.select_one('.anime_info.main.site-content-float .anime_names')
                if title_info:
                    title_localized = title_info.select_one('.entity_localized_name')
                    title_original = title_info.select_one('.entity_original_name')
                    if title_localized:
                        anime_data[anime_key]["title_localized"] = title_localized.get_text(strip=True)
                    if title_original:
                        anime_data[anime_key]["title_original"] = title_original.get_text(strip=True)

                for music in block.select('.anime_music'):
                    kind_tag = music.select_one('.song_type')
                    song_tag = music.select_one('.song_name')
                    artist_tag = music.select_one('.singer span')
                    singer_tag = music.select_one('.singer')

                    if not kind_tag or not song_tag:
                        continue

                    kind = kind_tag.get_text(strip=True).lower()
                    title = song_tag.get_text(strip=True)
                    artist = artist_tag.get_text(strip=True) if artist_tag else "未知"
                    singer = singer_tag.get_text(strip=True) if singer_tag else "未知"

                    if kind in ['op', 'ed']:
                        anime_data[anime_key][kind].append({
                            "title": title,
                            "artist": artist,
                            "singer": singer,
                            "link": None
                        })

            return {season_key: dict(anime_data)}

        except Exception as e:
            print(f"❌ 抓取 {season_key} 發生錯誤：{e}")
            return {}

    @commands.command(name="updatesongs")
    async def manual_update(self, ctx, year: int, quarter: str):
        """手動更新指定季度的動畫歌曲資料。範例：!updatesongs 2024 Q2"""
        quarter = quarter.upper()
        valid_quarters = {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}
        if quarter not in valid_quarters:
            await ctx.send("❌ 季度格式錯誤，請使用 Q1~Q4 其中一個。")
            return

        season_key = f"{year}-{quarter}"
        month = valid_quarters[quarter]
        url = f"https://acgsecrets.hk/bangumi/{year}{month:02d}/"

        await ctx.send(f"📡 開始抓取動畫歌曲：{season_key}...")

        new_data = self.parse_acgsecrets_season(url, season_key)
        if not new_data or season_key not in new_data or not new_data[season_key]:
            await ctx.send(f"⚠️ 未找到 {season_key} 的任何資料，請確認該季度是否存在。")
            return

        # 讀取現有資料
        if os.path.exists(ANIME_SONG_DATA_FILE):
            with open(ANIME_SONG_DATA_FILE, "r", encoding="utf-8") as f:
                current_data = json.load(f)
        else:
            current_data = {}

        # 更新指定季度
        current_data[season_key] = new_data[season_key]

        # 寫入檔案
        with open(ANIME_SONG_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(current_data, f, ensure_ascii=False, indent=2)

        await ctx.send(f"✅ 已更新動畫歌曲資料：{season_key}！")

# 註冊 Cog
async def setup(bot):
    await bot.add_cog(AnimeSongFetcher(bot))
