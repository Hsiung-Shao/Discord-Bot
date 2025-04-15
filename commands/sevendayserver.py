import os
import subprocess
import psutil
import asyncio
from datetime import datetime
import telnetlib
from discord.ext import commands
from config import SEVENDAY_DIR, SEVENDAY_EXE, SEVENDAY_KEYWORD, SEVENDAY_TELNET_PORT, SEVENDAY_TELNET_PASSWORD

class SevenDayServerControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.base_path = SEVENDAY_DIR
        self.exe_file = SEVENDAY_EXE
        self.keyword = SEVENDAY_KEYWORD
        self.telnet_port = SEVENDAY_TELNET_PORT
        self.telnet_password = SEVENDAY_TELNET_PASSWORD
        self.last_started = None
        self.last_backup = None

    def is_process_running(self):
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                if proc.info['name'] and self.keyword in proc.info['name']:
                    return True
            except:
                continue
        return False

    @commands.command(name="start7d")
    async def start_server(self, ctx):
        if self.is_process_running():
            print("⚠️ 7 Days 已在執行中")
            return False
        try:
            subprocess.Popen(
                os.path.join(self.base_path, self.exe_file),
                cwd=self.base_path,
                shell=True
            )
            self.last_started = datetime.now()
            print("✅ 7 Days 啟動成功")
            return True
        except Exception as e:
            print(f"❌ 7 Days 啟動失敗：{e}")
            return None

    @commands.command(name="stop7d")
    async def stop_server(self, ctx):
        if not self.is_process_running():
            print("⚠️ 7 Days 尚未啟動")
            return False
        try:
            with telnetlib.Telnet("127.0.0.1", self.telnet_port, timeout=10) as tn:
                tn.read_until(b"Please enter password:", timeout=5)
                tn.write(self.telnet_password.encode("utf-8") + b"\n")
                tn.read_until(b"\n", timeout=5)
                await asyncio.sleep(3)
                tn.write(b"shutdown\n")
            print("🛑 7 Days 關閉成功")
            return True
        except Exception as e:
            print(f"❌ 7 Days 關閉失敗：{e}")
            return None


async def setup(bot):
    await bot.add_cog(SevenDayServerControl(bot))
