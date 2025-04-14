# core/server_manager.py
import subprocess
import psutil
import os
from utils.logger import get_logger

logger = get_logger("ServerManager")

class ServerManager:
    def __init__(self, name: str, jar_path: str, working_dir: str, keyword: str):
        self.name = name
        self.jar_path = jar_path
        self.working_dir = working_dir
        self.keyword = keyword
        self.process = None

    def is_running(self) -> bool:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if any(self.keyword in str(arg) for arg in proc.info['cmdline']):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def start_server(self):
        if self.is_running():
            logger.info(f"{self.name} 已在執行中。")
            return

        logger.info(f"啟動 {self.name} 中...")
        self.process = subprocess.Popen(
            ["java", "-jar", self.jar_path, "nogui"],
            cwd=self.working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

    def stop_server(self):
        logger.info(f"準備關閉 {self.name}...")
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if any(self.keyword in str(arg) for arg in proc.info['cmdline']):
                    proc.terminate()
                    proc.wait(timeout=10)
                    logger.info(f"{self.name} 已關閉。")
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                logger.warning(f"無法正常終止 {self.name} 的進程。")
        logger.info(f"{self.name} 未在執行中。")
