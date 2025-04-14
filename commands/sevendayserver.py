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
    SEVENDAY_SAVE_PATH
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

                # 等待登入成功回應
                login_response = tn.read_until(b">", timeout=10)
                print(f"🔐 登入成功回應：{login_response.decode(errors='ignore')}")

                await asyncio.sleep(2)  # 保險等待

                # 寫入 shutdown 並加上換行
                tn.write(b"shutdown\n")
                print("✅ 已發送 shutdown 指令")

                await self.send_status_embed("🛑 關閉指令已送出，伺服器將關閉。", discord.Color.red())

        except Exception as e:
            await self.send_status_embed(f"❌ 關閉失敗：```{e}```", discord.Color.red())


async def setup(bot):
    await bot.add_cog(SevenDayServerControl(bot))
