import os
import subprocess
import psutil
import asyncio
from datetime import datetime
import telnetlib
from discord.ext import commands
from utils.logger import clear_channel_log, get_logger
from config import (
    SEVENDAY_DIR, SEVENDAY_EXE, SEVENDAY_KEYWORD, SEVENDAY_CMDLINE_KEYWORD,
    SEVENDAY_START_TASK, SEVENDAY_TELNET_PORT, SEVENDAY_TELNET_PASSWORD,
)

logger = get_logger(__name__, channel="sevenday")

class SevenDayServerControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.base_path = SEVENDAY_DIR
        self.exe_file = SEVENDAY_EXE
        self.keyword = SEVENDAY_KEYWORD
        self.cmdline_keyword = SEVENDAY_CMDLINE_KEYWORD
        self.start_task = SEVENDAY_START_TASK
        self.telnet_port = SEVENDAY_TELNET_PORT
        self.telnet_password = SEVENDAY_TELNET_PASSWORD
        self.last_started = None
        self.last_backup = None

    def is_process_running(self):
        """程序名稱含 SEVENDAY_KEYWORD 即視為伺服器;若設了 SEVENDAY_CMDLINE_KEYWORD,
        命令列還必須含該字串(例如 -dedicated),避免把同名的遊戲客戶端當成伺服器。"""
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = proc.info['name'] or ""
                if self.keyword not in name:
                    continue
                if self.cmdline_keyword:
                    cmdline = " ".join(proc.info['cmdline'] or [])
                    if not cmdline:
                        # 讀不到 cmdline(如對方以系統管理員執行)時無從分辨,
                        # 保守視為伺服器在跑,避免重複啟動撞 port
                        logger.warning(f"⚠️ 偵測到 {self.keyword}(PID {proc.info.get('pid')})但讀不到命令列,保守視為伺服器執行中")
                        return True
                    if self.cmdline_keyword not in cmdline:
                        continue
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return False

    @commands.command(name="start7d")
    async def start_server(self, ctx):
        if self.is_process_running():
            logger.warning("⚠️ 7 Days 已在執行中")
            return False
        # 每次啟動清空 sevenday.log,讓本次運行的紀錄純淨
        clear_channel_log("sevenday")
        logger.info("🆕 7 Days 啟動流程開始,sevenday.log 已重置")
        try:
            if self.start_task:
                # 服務(session 0)開的視窗看不見;改觸發使用者 session 的排程任務,console 會留在桌面
                r = subprocess.run(
                    ["schtasks", "/Run", "/TN", self.start_task],
                    capture_output=True, text=True, timeout=30,
                )
                if r.returncode != 0:
                    logger.error(f"❌ 7 Days 排程任務 {self.start_task} 觸發失敗（使用者未登入?）：{(r.stderr or r.stdout).strip()}")
                    return None
            else:
                subprocess.Popen(
                    os.path.join(self.base_path, self.exe_file),
                    cwd=self.base_path,
                    shell=True
                )
            self.last_started = datetime.now()
            logger.info("✅ 7 Days 啟動成功")
            if self.bot and hasattr(self.bot, "backup_task"):
                self.bot.backup_task.start()
            return True
        except Exception as e:
            logger.error(f"❌ 7 Days 啟動失敗：{e}")
            return None

    @commands.command(name="stop7d")
    async def stop_server(self, ctx):
        if not self.is_process_running():
            logger.warning("⚠️ 7 Days 尚未啟動")
            return False
        try:
            with telnetlib.Telnet("127.0.0.1", self.telnet_port, timeout=10) as tn:
                # TelnetPassword 留空時 7DTD 只監聽 loopback 且不會要密碼,直接送指令即可
                if self.telnet_password:
                    tn.read_until(b"Please enter password:", timeout=5)
                    tn.write(self.telnet_password.encode("utf-8") + b"\n")
                    tn.read_until(b"\n", timeout=5)
                    await asyncio.sleep(3)
                tn.write(b"shutdown\n")
            logger.info("🛑 7 Days 關閉成功")
            if self.bot and hasattr(self.bot, "backup_task"):
                asyncio.create_task(self._stop_backup_after_delay())
            return True
        except Exception as e:
            logger.error(f"❌ 7 Days 關閉失敗：{e}")
            return None

    async def _stop_backup_after_delay(self):
        await asyncio.sleep(300)
        if hasattr(self.bot, "backup_task"):
            self.bot.backup_task.stop()
            logger.info("📦 自動備份任務已關閉")

async def setup(bot):
    await bot.add_cog(SevenDayServerControl(bot))
