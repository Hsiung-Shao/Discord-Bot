import subprocess
import psutil
import os
import threading
from utils.logger import get_logger
from datetime import datetime

logger = get_logger("ServerManager")

class ServerManager:
    def __init__(self, name: str, jar_path: str, working_dir: str, keyword: str):
        self.name = name
        self.jar_path = jar_path  # 實際上這裡為 .bat 檔案名稱
        self.working_dir = working_dir
        self.keyword = keyword
        self.process = None

    def is_running(self) -> bool:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if cmdline and any(self.keyword in str(arg) for arg in cmdline):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, PermissionError) as e:
                logger.debug(f"[DEBUG] 無法讀取進程：{e}")
                continue
        return False

    def start_server(self):
        if self.is_running():
            logger.info(f"{self.name} 已在執行中。")
            return

        bat_file_path = os.path.join(self.working_dir, self.jar_path)
        if not os.path.isfile(bat_file_path):
            logger.warning(f"找不到啟動檔：{bat_file_path}")
            return

        log_path = os.path.join("logs", f"{self.name.lower()}_{datetime.now():%Y%m%d_%H%M%S}.log")
        os.makedirs("logs", exist_ok=True)

        logger.info(f"啟動 {self.name}，log 將同步輸出至 terminal 並寫入：{log_path}")

        self.process = subprocess.Popen(
            bat_file_path,
            cwd=self.working_dir,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8"
        )

        # ✅ 使用 background thread 處理 log 輸出與寫入檔案，避免阻塞
        def stream_log():
            with open(log_path, "w", encoding="utf-8") as logfile:
                for line in self.process.stdout:
                    if not line:
                        break
                    line = line.strip()
                    print(f"[{self.name}] {line}")
                    logfile.write(f"{line}\n")

        threading.Thread(target=stream_log, daemon=True).start()

    def stop_server(self):
        logger.info(f"準備關閉 {self.name}...")
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline')
                if cmdline and any(self.keyword in str(arg) for arg in cmdline):
                    proc.terminate()
                    proc.wait(timeout=10)
                    logger.info(f"{self.name} 已關閉。")
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                logger.warning(f"無法正常終止 {self.name} 的進程。")
        logger.info(f"{self.name} 未在執行中。")
