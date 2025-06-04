# config.py
import os
from dotenv import load_dotenv

load_dotenv()  # 讀取 .env 檔案

# Discord Bot
BOT_TOKEN = os.getenv("BOT_TOKEN")
CONTROL_THREAD_ID = os.getenv("CONTROL_THREAD_ID")

# Minecraft Server
MINECRAFT_JAR_KEYWORD = os.getenv("MINECRAFT_JAR_KEYWORD")
MINECRAFT_START_BAT = os.getenv("MINECRAFT_START_BAT")
MINECRAFT_BASE_PATH = os.getenv("MINECRAFT_BASE_PATH")
MINECRAFT_RCON_PORT = int(os.getenv("MINECRAFT_RCON_PORT"))
MINECRAFT_RCON_PASSWORD = os.getenv("MINECRAFT_RCON_PASSWORD")
MINECRAFT_STATUS_THREAD_ID = int(os.getenv("MINECRAFT_STATUS_THREAD_ID"))


# 7 Days to Die Server
SEVENDAY_DIR = os.getenv("SEVENDAY_DIR")
SEVENDAY_EXE = os.getenv("SEVENDAY_EXE")
SEVENDAY_KEYWORD = os.getenv("SEVENDAY_KEYWORD")
SEVENDAY_TELNET_PORT = int(os.getenv("SEVENDAY_TELNET_PORT"))
SEVENDAY_TELNET_PASSWORD = os.getenv("SEVENDAY_TELNET_PASSWORD")
SEVENDAY_STATUS_THREAD_ID = int(os.getenv("SEVENDAY_STATUS_THREAD_ID"))
SEVENDAY_SAVE_PATH = os.getenv("SEVENDAY_SAVE_PATH")
# 資料轉發配置檔案
FORWARDER_CONFIG = os.getenv("FORWARDER_CONFIG", "data/forwarder_map.json")
BDNEWS_DATA_FILE = os.getenv("BDNEWS_DATA_FILE", "data/news_data.json")

# BD2 News channel ID
BDUST_NEWS_THREAD_ID = int(os.getenv("BDNEWS_THREAD_ID", 0))
BDUST_REMINDER_CHANNEL_ID = int(os.getenv("BDUST_REMINDER_CHANNEL_ID", "0"))
BDUST_DATA_FILE = "data/news_data.json"
BDUST_REMIND_USERS_FILE = "data/bdust_remind_users.json"


ANIME_SONG_DATA_FILE = os.getenv("ANIME_SONG_DATA_FILE")

# Backup Directory
BACKUP_ROOT = os.getenv("BACKUP_ROOT")

# Riot Games News
VALORANT_BASE_URL = "https://playvalorant.com"
VALORANT_THREAD_ID = int(os.getenv("VALORANT_THREAD_ID", 0))
LOL_BASE_URL = "https://www.leagueoflegends.com"
LOL_THREAD_ID = int(os.getenv("LOL_THREAD_ID", 0))
RIOT_DATA_FILE = os.getenv("RIOT_DATA_FILE", "data/riotnews.json")