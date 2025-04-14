# config.py
import os
from dotenv import load_dotenv

load_dotenv()  # 讀取 .env 檔案

# Discord Bot
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Minecraft Server
MINECRAFT_JAR = os.getenv("MINECRAFT_JAR")
MINECRAFT_DIR = os.getenv("MINECRAFT_DIR")
MINECRAFT_KEYWORD = os.getenv("MINECRAFT_KEYWORD")

# 7 Days to Die Server
SEVENDAY_DIR = os.getenv("SEVENDAY_DIR")
SEVENDAY_EXE = os.getenv("SEVENDAY_EXE")
SEVENDAY_KEYWORD = os.getenv("SEVENDAY_KEYWORD")

# 資料轉發配置檔案
FORWARDER_CONFIG = os.getenv("FORWARDER_CONFIG", "data/forwarder_map.json")
BDNEWS_DATA_FILE = os.getenv("BDNEWS_DATA_FILE", "data/news_data.json")

# BD2 News channel ID
BDNEWS_THREAD_ID = int(os.getenv("BDNEWS_THREAD_ID", 0))
