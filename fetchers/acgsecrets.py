from collections import defaultdict
from bs4 import BeautifulSoup
import requests

def parse_acgsecrets_season(url: str, season_key: str) -> dict:
    res = requests.get(url)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    anime_data = defaultdict(lambda: {"op": [], "ed": []})

    anime_blocks = soup.select('div.spannable-main-content')  # 每部動畫的主容器

    for block in anime_blocks:
        # 動畫名稱處理
        title_local = block.select_one('.entity_localized_name')
        title_original = block.select_one('.entity_original_name')

        if not title_local and not title_original:
            continue

        localized_name = title_local.get_text(strip=True) if title_local else "未知"
        original_name = title_original.get_text(strip=True) if title_original else "未知"
        title_key = f"{localized_name} ({original_name})" if localized_name != original_name else localized_name

        # 初始化資料結構
        anime_data[title_key] = {
            "op": [],
            "ed": [],
            "localized_name": localized_name,
            "original_name": original_name
        }

        # 樂曲抓取區塊
        music_blocks = block.select('.anime_music')
        for music in music_blocks:
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
                anime_data[title_key][kind].append({
                    "title": title,
                    "artist": artist,
                    "singer": singer,
                    "link": None
                })

    return {season_key: dict(anime_data)}



if __name__ == "__main__":
    url = "https://acgsecrets.hk/bangumi/202504/"
    data = parse_acgsecrets_season(url, "2025-Q2")

    import json
    with open("data/anime_songs.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("✅ 實際歌曲資料已寫入 anime_songs.json")

