import os
import asyncio
from backups.minecraft_backup import MinecraftBackupHandler
from backups.seven_days_backup import SevenDaysBackupHandler
from config import BACKUP_ROOT  # 設定備份根目錄

class BackupManager:
    def __init__(self):
        self.handlers = []

    def register_handler(self, handler):
        self.handlers.append(handler)

    async def backup_all(self):
        results = []
        for handler in self.handlers:
            try:
                path = await handler.perform_backup()
                results.append((handler.name, f"✅ 備份成功：{os.path.basename(path)}"))
            except Exception as e:
                results.append((handler.name, f"❌ 備份失敗：{e}"))
        return results

