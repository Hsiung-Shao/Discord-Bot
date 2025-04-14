import os
import subprocess
import asyncio
import discord
import psutil
import telnetlib
import shutil
import tempfile
import datetime
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from config import (
    SEVENDAY_DIR,
    SEVENDAY_EXE,
    SEVENDAY_KEYWORD,
    SEVENDAY_TELNET_PORT,
    SEVENDAY_TELNET_PASSWORD,
    SEVENDAY_STATUS_THREAD_ID,
    SEVENDAY_SAVE_PATH,
)

class SevenDayServerControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server_path = SEVENDAY_DIR
        self.exe_name = SEVENDAY_EXE
        self.keyword = SEVENDAY_KEYWORD
        self.telnet_port = SEVENDAY_TELNET_PORT
        self.telnet_password = SEVENDAY_TELNET_PASSWORD
        self.status_thread_id = SEVENDAY_STATUS_THREAD_ID
        self.world_path = SEVENDAY_SAVE_PATH
        self.backup_path = os.path.join("backups", "7d2d")
        os.makedirs(self.backup_path, exist_ok=True)

        self.scheduler = None  # 延後啟動

    def is_process_running(self):
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] and self.keyword.lower() in proc.info['name'].lower():
                    return True
            except Exception:
                continue
        return False

    async def send_status_embed(self, content: str, color=discord.Color.dark_gold()):
        try:
            thread = await self.bot.fetch_channel(self.status_thread_id)
            if isinstance(thread, discord.Thread):
                embed = discord.Embed(description=content.strip(), color=color)
                await thread.send(embed=embed)
        except Exception as e:
            print(f"❌ Discord 通知錯誤：{e}")

    @commands.command(name="start7d")
    async def start_server(self, ctx):
        if self.is_process_running():
            await self.send_status_embed("⚠️ 7 Days to Die 伺服器已在執行中。", discord.Color.orange())
            return

        try:
            subprocess.Popen(
                self.exe_name,
                cwd=self.server_path,
                shell=True
            )
            await self.send_status_embed("✅ 啟動指令已送出，伺服器正在啟動中...", discord.Color.green())

            # 啟動備份排程器
            if self.scheduler is None:
                self.scheduler = AsyncIOScheduler()
                self.scheduler.add_job(self.auto_backup_task, IntervalTrigger(hours=1))
                self.scheduler.start()
                print("✅ SevenDayServer 備份排程器已啟動，每小時執行一次")

        except Exception as e:
            await self.send_status_embed(f"❌ 啟動失敗：```{e}```", discord.Color.red())

    @commands.command(name="stop7d")
    async def stop_server(self, ctx):
        if not self.is_process_running():
            await self.send_status_embed("⚠️ 伺服器尚未執行，無需關閉。", discord.Color.orange())
            return

        try:
            print("嘗試連接 Telnet...")
            with telnetlib.Telnet("127.0.0.1", self.telnet_port, timeout=10) as tn:
                tn.read_until(b"Please enter password:", timeout=10)
                tn.write(self.telnet_password.encode("utf-8") + b"\n")
                login_response = tn.read_until(b">", timeout=10)
                print(f"🔐 Telnet 登入成功回應：{login_response.decode(errors='ignore')}")
                await asyncio.sleep(2)
                tn.write(b"shutdown\n")
                print("✅ 已發送 shutdown 指令")

            await self.send_status_embed("🛑 關閉指令已送出，伺服器將關閉。", discord.Color.red())

            # 關閉備份排程器
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
                self.scheduler = None
                print("🛑 SevenDayServer 備份排程器已關閉")

        except Exception as e:
            await self.send_status_embed(f"❌ 關閉失敗：```{e}```", discord.Color.red())

    def backup_world(self):
        try:
            if not os.path.exists(self.world_path):
                print(f"[備份] ❌ 找不到世界存檔路徑：{self.world_path}")
                return False, None

            now = datetime.datetime.now()
            timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
            temp_dir = tempfile.mkdtemp()
            temp_world = os.path.join(temp_dir, "world")

            shutil.copytree(self.world_path, temp_world)

            zip_path = os.path.join(self.backup_path, f"backup_{timestamp}.zip")
            shutil.make_archive(zip_path.replace(".zip", ""), 'zip', temp_world)

            shutil.rmtree(temp_dir)

            print(f"[備份] ✅ 成功：{zip_path}")
            return True, zip_path
        except Exception as e:
            print(f"[備份] ❌ 發生錯誤：{e}")
            return False, None

    def clear_old_backups(self, hours=36):
        cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
        removed = 0
        for filename in os.listdir(self.backup_path):
            if filename.startswith("backup_") and filename.endswith(".zip"):
                full_path = os.path.join(self.backup_path, filename)
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(full_path))
                if mtime < cutoff:
                    os.remove(full_path)
                    removed += 1
                    print(f"[備份] 🧹 刪除過期備份：{filename}")
        if removed:
            print(f"[備份] ✅ 已刪除 {removed} 筆超過 {hours} 小時的備份")

    async def auto_backup_task(self):
        if not self.is_process_running():
            print("[備份] ⚠️ 伺服器未啟動，跳過備份")
            return
        print("[備份] ⏳ 執行中...")
        ok, path = self.backup_world()
        if ok:
            self.clear_old_backups()

async def setup(bot):
    await bot.add_cog(SevenDayServerControl(bot))
