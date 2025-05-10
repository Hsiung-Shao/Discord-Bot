import os
import gzip
import shutil
from datetime import datetime
from discord.ext import tasks

LOG_DIR = r"F:\coding\project\Python\Discord Bot\Discord Bot\logs"

class LogCompressor:
    def __init__(self, bot):
        self.bot = bot
        self.compress_old_logs_task.start()

    @tasks.loop(hours=24)
    async def compress_old_logs_task(self):
        now = datetime.now()
        for filename in os.listdir(LOG_DIR):
            if filename.endswith(".log") and "bot.log" in filename:
                filepath = os.path.join(LOG_DIR, filename)
                try:
                    date_str = filename.split(".")[-2]
                    log_date = datetime.strptime(date_str, "%Y-%m-%d")
                    if (now - log_date).days >= 30:
                        gz_path = filepath + ".gz"
                        with open(filepath, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
                        os.remove(filepath)
                        print(f"[LOG-COMPRESSOR] 壓縮完成：{filename}")
                except Exception as e:
                    print(f"[LOG-COMPRESSOR] 跳過 {filename}，原因：{e}")
