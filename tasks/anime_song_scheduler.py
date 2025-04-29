# tasks/song_scheduler.py

import discord
from discord.ext import tasks
import datetime
import os
import json
from config import ANIME_SONG_DATA_FILE
from fetchers.acgsecrets import parse_acgsecrets_season

def get_quarter(month: int) -> str:
    if month in [1, 2, 3]:
        return "Q1"
    elif month in [4, 5, 6]:
        return "Q2"
    elif month in [7, 8, 9]:
        return "Q3"
    else:
        return "Q4"

def get_quarter_start_month(quarter: str) -> int:
    return {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}[quarter]

def get_target_season_for_today() -> tuple[int, str] | tuple[None, None]:
    today = datetime.date.today()
    year = today.year
    month = today.month

    if month in [2, 5, 8, 11]:  # 補完更新
        quarter = get_quarter(month - 1)
        return (year, quarter)
    elif month in [1, 4, 7, 10]:  # 初次更新
        quarter = get_quarter(month)
        return (year, quarter)
    else:
        return (None, None)

async def auto_update_anime_songs():
    year, quarter = get_target_season_for_today()
    if not year or not quarter:
        print("📅 今日不在自動更新日範圍內，略過更新。")
        return

    season_key = f"{year}-{quarter}"
    month = get_quarter_start_month(quarter)
    url = f"https://acgsecrets.hk/bangumi/{year}{month:02d}/"

    print(f"📡 自動更新動畫歌曲資料：{season_key}")

    try:
        new_data = parse_acgsecrets_season(url, season_key)
        if not new_data or season_key not in new_data:
            print(f"⚠️ 未能成功抓取 {season_key}，無更新動作。")
            return

        if os.path.exists(ANIME_SONG_DATA_FILE):
            with open(ANIME_SONG_DATA_FILE, "r", encoding="utf-8") as f:
                current_data = json.load(f)
        else:
            current_data = {}

        current_data[season_key] = new_data[season_key]

        with open(ANIME_SONG_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(current_data, f, ensure_ascii=False, indent=2)

        print(f"✅ 成功更新動畫歌曲資料：{season_key}")
    except Exception as e:
        print(f"❌ 自動更新失敗：{e}")

# 提供給 bot.py 呼叫
@tasks.loop(hours=24)
async def anime_song_updater():
    await auto_update_anime_songs()

def start_anime_song_updater():
    if not anime_song_updater.is_running():
        anime_song_updater.start()
