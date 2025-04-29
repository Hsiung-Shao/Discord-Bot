# commands/anime_songs.py

import discord
from discord.ext import commands
import os
import json
from config import ANIME_SONG_DATA_FILE
from fetchers.acgsecrets import fetch_season_data

class AnimeSongFetcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        os.makedirs(os.path.dirname(ANIME_SONG_DATA_FILE), exist_ok=True)

    @commands.command(name="updatesongs")
    async def manual_update(self, ctx, year: int, quarter: str):
        """手動更新指定季度的動畫歌曲資料。用法範例：!updatesongs 2024 Q2"""
        quarter = quarter.upper()
        valid_quarters = {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}
        if quarter not in valid_quarters:
            await ctx.send("❌ 季度格式錯誤，請使用 Q1~Q4。")
            return

        season_key = f"{year}-{quarter}"
        month = valid_quarters[quarter]

        await ctx.send(f"📡 開始抓取動畫歌曲資料：`{season_key}`...")

        season_data = fetch_season_data(year, month)
        if not season_data or season_key not in season_data:
            await ctx.send(f"⚠️ `{season_key}` 無法取得有效資料，請稍後再試或確認該季度是否存在。")
            return

        if os.path.exists(ANIME_SONG_DATA_FILE):
            with open(ANIME_SONG_DATA_FILE, "r", encoding="utf-8") as f:
                current_data = json.load(f)
        else:
            current_data = {}

        current_data[season_key] = season_data[season_key]

        with open(ANIME_SONG_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(current_data, f, ensure_ascii=False, indent=2)

        await ctx.send(f"✅ `{season_key}` 動畫歌曲資料更新完成！")

async def setup(bot):
    await bot.add_cog(AnimeSongFetcher(bot))
