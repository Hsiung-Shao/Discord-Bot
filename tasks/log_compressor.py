"""舊 log 壓縮與過期清理。

utils/logger.py 的 TimedRotatingFileHandler 每天午夜把 `<name>.log` 切成
`<name>.log.YYYY-MM-DD.log`。本任務每 7 天跑一輪:

- 切檔超過 7 天的 `*.log.YYYY-MM-DD.log` → gzip 成同名 `.gz` 後刪原檔
- 任何切檔日期超過 14 天的檔案(不論已壓縮與否)→ 直接刪除

日期以檔名裡的 YYYY-MM-DD 為準,解析不出來的檔案一律跳過(例如 bot.log、
stdout.log、stderr.log 這些沒有日期的主檔)。handler 自己的 backupCount 只會清
`YYYY-MM-DD.log` 結尾的檔,不會碰 `.gz`,所以 .gz 的生命週期完全由這裡負責。

正常壓縮/刪除不留紀錄,只有失敗才寫 bot.log。
"""

import asyncio
import gzip
import logging
import os
import re
import shutil
from datetime import datetime

from discord.ext import tasks

from utils.logger import get_logger, log_dir

logger = get_logger(__name__, level=logging.WARNING)

COMPRESS_AFTER_DAYS = 7     # 切檔滿幾天壓成 .gz
DELETE_AFTER_DAYS = 14      # 切檔滿幾天刪除(含 .gz)
RUN_EVERY_HOURS = 24 * 7    # 任務執行間隔

# 例:bot.log.2026-08-01.log / bot.log.2026-08-01.log.gz
_ROTATED_RE = re.compile(
    r"^(?P<base>.+)\.log\.(?P<date>\d{4}-\d{2}-\d{2})\.log(?P<gz>\.gz)?$"
)


def compress_once(directory: str | None = None, now: datetime | None = None) -> tuple[int, int]:
    """同步執行一輪壓縮與清理,回傳 (壓縮數, 刪除數)。

    `directory` / `now` 可注入,方便測試;預設用專案 logs/ 與目前時間。
    """
    directory = directory or log_dir
    now = now or datetime.now()
    compressed = deleted = 0

    try:
        filenames = os.listdir(directory)
    except OSError as e:
        logger.error(f"[LOG-COMPRESSOR] 無法列出 {directory}:{e}")
        return compressed, deleted

    for filename in filenames:
        m = _ROTATED_RE.match(filename)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group("date"), "%Y-%m-%d")
        except ValueError:
            continue

        age_days = (now - file_date).days
        path = os.path.join(directory, filename)
        try:
            if age_days >= DELETE_AFTER_DAYS:
                os.remove(path)
                deleted += 1
            elif not m.group("gz") and age_days >= COMPRESS_AFTER_DAYS:
                gz_path = path + ".gz"
                with open(path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.remove(path)
                compressed += 1
        except OSError as e:
            logger.error(f"[LOG-COMPRESSOR] 處理 {filename} 失敗:{e}")

    return compressed, deleted


class LogCompressor:
    def __init__(self, bot):
        self.bot = bot
        self.compress_old_logs_task.start()

    @tasks.loop(hours=RUN_EVERY_HOURS)
    async def compress_old_logs_task(self):
        try:
            await asyncio.to_thread(compress_once)
        except Exception as e:
            logger.error(f"[LOG-COMPRESSOR] 本輪執行失敗:{e}")

    @compress_old_logs_task.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()
