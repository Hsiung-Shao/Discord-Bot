import logging
import os
from datetime import datetime

# 使用絕對路徑設定 log 目錄
log_dir = r"F:\coding\project\Python\Discord Bot\Discord Bot\logs"
os.makedirs(log_dir, exist_ok=True)

# 設定 log 檔案名稱
log_filename = os.path.join(log_dir, f"{datetime.now():%Y-%m-%d}.log")

# 設定 logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
