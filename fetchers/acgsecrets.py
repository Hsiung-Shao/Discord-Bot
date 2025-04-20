import os
import json
import requests
import subprocess
from bs4 import BeautifulSoup
from collections import defaultdict

DATA_FOLDER = "data"
DATA_FILE = os.path.join(DATA_FOLDER, "anime_songs.json")
os.makedirs(DATA_FOLDER, exist_ok=True)

SEASON_MONTHS = {1: "Q1", 4: "Q2", 7: "Q3", 10: "Q4"}


def search_youtube_link_yt_dlp(query: str) -> str | None:
    try:
        command = [
            "yt-dlp",
            f"ytsearch1:{query}",
            "--print", "%(webpage_url)s",
            "--no-warnings",
            "--skip-download"
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
        url = result.stdout.strip()
        return url if url.startswith("http") else None
    except Exception as e:
        print(f"❌ yt-dlp 搜尋錯誤：{e}")
        return None


def parse_acgsecrets_season(url: str, season_key: str) -> dict:
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

            title_info = block.select_one('.anime_info.main.site-content-float .anime_names')
            if title_info:
                title_localized = title_info.select_one('.entity_localized_name')
                title_original = title_info.select_one('.entity_original_name')
                anime_key = title_localized.get_text(strip=True) if title_localized else data_tag.get_text(strip=True)
                anime_data[anime_key]["title_localized"] = anime_key
                anime_data[anime_key]["title_original"] = title_original.get_text(strip=True) if title_original else ""
            else:
                continue

            yt_links = [a["href"] for a in block.select('a.youtube[href]') if "youtube.com/watch" in a["href"]]
            yt_index = 0

            for music in block.select('.anime_music'):
                kind_tag = music.select_one('.song_type')
                song_tag = music.select_one('.song_name')
                artist_tag = music.select_one('.singer span')
                singer_tag = music.select_one('.singer')

                if not kind_tag or not song_tag:
                    continue

                kind = kind_tag.get_text(strip=True).lower()
                musictitle = song_tag.get_text(strip=True)
                artist = artist_tag.get_text(strip=True) if artist_tag else "未知"
                singer = singer_tag.get_text(strip=True) if singer_tag else "未知"

                link = yt_links[yt_index] if yt_index < len(yt_links) else None
                yt_index += 1

                if not link:
                    query = f"{musictitle} {singer}"
                    link = search_youtube_link_yt_dlp(query)

                if kind in ['op', 'ed']:
                    anime_data[anime_key][kind].append({
                        "musictitle": musictitle,
                        "artist": artist,
                        "singer": singer,
                        "link": link
                    })

        return {season_key: dict(anime_data)}

    except Exception as e:
        print(f"❌ 抓取 {season_key} 發生錯誤：{e}")
        return {}


def batch_fetch(start_year=2017, end_year=2025, end_quarter=2):
    all_data = {}
    for year in range(start_year, end_year + 1):
        for month in [1, 4, 7, 10]:
            quarter = SEASON_MONTHS[month]
            if year == end_year and quarter > f"Q{end_quarter}":
                break

            season_key = f"{year}-{quarter}"
            url = f"https://acgsecrets.hk/bangumi/{year}{month:02d}/"
            print(f"📡 正在抓取：{season_key}")
            season_data = parse_acgsecrets_season(url, season_key)

            if season_data:
                all_data.update(season_data)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"\n📦 所有季度資料已寫入 {DATA_FILE}")

if __name__ == "__main__":
    batch_fetch()
