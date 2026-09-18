import os
import asyncio
import tempfile
import shutil
from datetime import datetime, timedelta
import logging
from utils.logger import get_logger

logger = get_logger(__name__, level=logging.WARNING)

class AutoBackupTask:
    def __init__(self, bot, interval_minutes=60, retention_hours=36):
        self.bot = bot
        self.interval = interval_minutes * 60
        self.retention = timedelta(hours=retention_hours)
        self.task = None
        self.active = False

    def start(self):
        if not self.task:
            self.active = True
            self.task = asyncio.create_task(self._run())
            logger.info("🌀 自動備份任務已啟動")

    def stop(self):
        self.active = False
        logger.info("🛑 自動備份任務標記為停止，將於下一輪備份後結束")

    async def _run(self):
        await asyncio.sleep(10)  # 延遲啟動，避免與 server 啟動衝突
        while self.active:
            results = []

            for handler in self.bot.backup_manager.handlers:
                try:
                    temp_dir = tempfile.mkdtemp()
                    temp_zip_path = await handler.perform_backup(temp_dir)

                    # 強化處理 SevenDays 特例路徑格式與安全性
                    if not hasattr(handler, 'get_final_path'):
                        raise AttributeError(f"Handler {handler.name} 缺少 get_final_path 方法")

                    final_path = shutil.move(temp_zip_path, handler.get_final_path(temp_zip_path))
                    results.append((handler.name, f"✅ 備份成功：{os.path.basename(final_path)}"))
                    self._cleanup_old_backups(handler)
                except Exception as e:
                    results.append((handler.name, f"❌ 備份失敗：{e.__class__.__name__} - {e}"))

            for name, result in results:
                logger.info(f"[備份結果] {name}: {result}")

            await asyncio.sleep(self.interval)
        self.task = None  # 停止後清除任務參考

    def _cleanup_old_backups(self, handler):
        now = datetime.now()
        try:
            folder = handler.backup_dir
            for f in os.listdir(folder):
                if f.endswith(".zip"):
                    fpath = os.path.join(folder, f)
                    mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                    if now - mtime > self.retention:
                        os.remove(fpath)
                        logger.info(f"🧹 已移除過期備份：{fpath}")
        except Exception as e:
            logger.warning(f"⚠️ 清除備份失敗（{handler.name}）：{e}")
