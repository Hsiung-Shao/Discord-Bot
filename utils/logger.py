import logging
import os
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
import gzip
import shutil

# === 絕對路徑設定 ===
# 獲取專案根目錄 (bot.py 所在位置)
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log_dir = os.path.join(base_dir, "logs")
os.makedirs(log_dir, exist_ok=True)

log_base_filename = os.path.join(log_dir, "bot.log")

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# === 設定每日切檔 ===
handler = TimedRotatingFileHandler(
    filename=log_base_filename,
    when="midnight",
    interval=1,
    backupCount=30,  # 控制未壓縮檔案數量
    encoding="utf-8",
    utc=False
)
handler.suffix = "%Y-%m-%d.log"

formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logger.addHandler(handler)
logger.addHandler(stream_handler)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)