import os
import zipfile
import asyncio
import shutil
import tempfile
from datetime import datetime
from backups.base_handler import BaseBackupHandler
from utils.logger import get_logger

logger = get_logger(__name__)

class SevenDaysBackupHandler(BaseBackupHandler):
    def __init__(self, save_path, backup_root):
        super().__init__(name="7 Days to Die")
        self.save_path = save_path
        self.server_folder = os.path.basename(os.path.dirname(self.save_path.rstrip('/\\')))
        self.backup_dir = os.path.join(backup_root, self.server_folder)
        os.makedirs(self.backup_dir, exist_ok=True)

    async def perform_backup(self, temp_dir=None):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        zip_filename = f"{self.server_folder}_save_{timestamp}.zip"

        if not temp_dir:
            temp_dir = tempfile.mkdtemp()

        temp_zip_path = os.path.join(temp_dir, zip_filename)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._zip_save, temp_zip_path)
            return temp_zip_path
        except Exception as e:
            logger.error(f"[7 Days] 備份失敗：{e.__class__.__name__} - {e}")
            raise

    def _zip_save(self, zip_path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(self.save_path):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.save_path)
                    zipf.write(full_path, arcname=rel_path)

    def get_final_path(self, zip_path):
        return os.path.join(self.backup_dir, os.path.basename(zip_path))

    def get_latest_backup_info(self):
        backups = sorted(
            (f for f in os.listdir(self.backup_dir) if f.endswith(".zip")),
            reverse=True
        )
        if backups:
            return os.path.join(self.backup_dir, backups[0])
        return None
