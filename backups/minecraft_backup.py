import os
import zipfile
import asyncio
from datetime import datetime
from backups.base_handler import BaseBackupHandler
from utils.logger import get_logger

logger = get_logger(__name__)

class MinecraftBackupHandler(BaseBackupHandler):
    def __init__(self, world_path, backup_root):
        super().__init__(name="Minecraft")
        self.world_path = world_path
        self.server_folder = os.path.basename(os.path.dirname(self.world_path.rstrip('/\\')))
        self.backup_dir = os.path.join(backup_root, self.server_folder)
        os.makedirs(self.backup_dir, exist_ok=True)

    async def perform_backup(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        zip_filename = os.path.join(self.backup_dir, f"{self.server_folder}_world_{timestamp}.zip")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._zip_world, zip_filename)
            logger.info(f"[Minecraft] 備份完成：{zip_filename}")
            return zip_filename
        except Exception as e:
            logger.error(f"[Minecraft] 備份失敗：{e.__class__.__name__} - {e}")
            raise

    def _zip_world(self, zip_path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(self.world_path):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.world_path)
                    zipf.write(full_path, arcname=rel_path)

    def get_latest_backup_info(self):
        backups = sorted(
            (f for f in os.listdir(self.backup_dir) if f.endswith(".zip")),
            reverse=True
        )
        if backups:
            return os.path.join(self.backup_dir, backups[0])
        return None
