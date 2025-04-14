import os
import subprocess
import asyncio
import discord
import psutil
import telnetlib
from discord.ext import commands
from config import (
    SEVENDAY_DIR,
    SEVENDAY_EXE,
    SEVENDAY_KEYWORD,
    SEVENDAY_TELNET_PORT,
    SEVENDAY_TELNET_PASSWORD,
    SEVENDAY_STATUS_THREAD_ID,
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
        self.status_thread = None
        print("✅ 資料夾存在？", os.path.exists("F:/Game Server/7DaysToDie"))
        print("✅ 啟動檔案存在？", os.path.exists("F:/Game Server/7DaysToDie/startdedicated.bat"))

    def is_process_running(self):
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any(self.keyword in str(arg) for arg in cmdline):
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
            print(f"❌ 7D2D 通知發送失敗：{e}")

    @commands.command(name="start7d")
    async def start_server(self, ctx):
        if self.is_process_running():
            await self.send_status_embed("⚠️ 7 Days to Die 伺服器已經在執行中，無需再次啟動。", discord.Color.orange())
            return

        try:
            subprocess.Popen(self.exe_name, cwd=self.server_path, shell=True)
            await self.send_status_embed("✅ 啟動指令已送出，正在等待伺服器啟動...")

            for _ in range(18):  # 最多等 90 秒
                if self.is_process_running():
                    await self.send_status_embed("🎉 7 Days to Die 伺服器已成功啟動！", discord.Color.green())
                    return
                await asyncio.sleep(5)

            await self.send_status_embed("⚠️ 無法確認伺服器是否啟動完成（逾時）", discord.Color.orange())

        except Exception as e:
            await self.send_status_embed(f"❌ 啟動失敗：```{e}```", discord.Color.red())

    @commands.command(name="stop7d")
    async def stop_server(self, ctx):
        if not self.is_process_running():
            await self.send_status_embed("⚠️ 伺服器尚未執行，無需關閉。", discord.Color.orange())
            return

        try:
            with telnetlib.Telnet("127.0.0.1", self.telnet_port, timeout=10) as tn:
                tn.read_until(b"Please enter password:")
                tn.write(self.telnet_password.encode("utf-8") + b"\n")
                tn.write(b"shutdown\n")
            await self.send_status_embed("🛑 已發送關閉指令，等待伺服器關閉中...", discord.Color.orange())

            for _ in range(20):  # 最多等 60 秒
                if not self.is_process_running():
                    await self.send_status_embed("✅ 7 Days to Die 伺服器已成功關閉。", discord.Color.red())
                    return
                await asyncio.sleep(3)

            await self.send_status_embed("⚠️ 關閉指令已送出，但伺服器仍未關閉（逾時）", discord.Color.orange())

        except Exception as e:
            await self.send_status_embed(f"❌ 關閉失敗：```{e}```", discord.Color.red())

async def setup(bot):
    await bot.add_cog(SevenDayServerControl(bot))
