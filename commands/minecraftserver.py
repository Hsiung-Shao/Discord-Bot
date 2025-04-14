import os
import subprocess
import psutil
import asyncio
import discord
from discord.ext import commands
from config import MINECRAFT_START_BAT
from mcrcon import MCRcon
from config import (
    MINECRAFT_JAR_KEYWORD,
    MINECRAFT_BASE_PATH,
    MINECRAFT_RCON_PORT,
    MINECRAFT_RCON_PASSWORD,
    MINECRAFT_STATUS_THREAD_ID
)

class MinecraftServerControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server_jar_keyword = MINECRAFT_JAR_KEYWORD
        self.server_base_path = MINECRAFT_BASE_PATH
        self.status_thread_id = MINECRAFT_STATUS_THREAD_ID
        self.rcon_host = "127.0.0.1"
        self.rcon_port = MINECRAFT_RCON_PORT
        self.rcon_password = MINECRAFT_RCON_PASSWORD

    def is_process_running(self):
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any(self.server_jar_keyword in str(arg) for arg in cmdline):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, PermissionError):
                continue
        return False

    async def send_status_embed(self, content: str, color=discord.Color.blue()):
        try:
            thread = await self.bot.fetch_channel(self.status_thread_id)
            if isinstance(thread, discord.Thread):
                embed = discord.Embed(description=content.strip(), color=color)
                await thread.send(embed=embed)
        except Exception as e:
            print(f"❌ Embed 傳送錯誤: {e}")

    @commands.command(name="startmc")
    async def start_server(self, ctx):
        print("🟢 startmc 指令觸發")
        if self.is_process_running():
            await self.send_status_embed("⚠️ 伺服器已經在執行中，無需再次啟動。", discord.Color.orange())
            return
        try:
            bat_path = os.path.join(self.server_base_path, MINECRAFT_START_BAT)
            subprocess.Popen(bat_path, cwd=self.server_base_path, shell=True)
            await self.send_status_embed("✅ Minecraft 伺服器啟動命令已發出。")
        except Exception as e:
            await self.send_status_embed(f"❌ 啟動失敗：```{e}```", discord.Color.red())

    @commands.command(name="stopmc")
    async def stop_server(self, ctx):
        try:
            with MCRcon(self.rcon_host, self.rcon_password, self.rcon_port) as mcr:
                mcr.command("say [Discord] 即將關閉伺服器")
                mcr.command("stop")
            await self.send_status_embed("🛑 Minecraft 伺服器關閉指令已發出。")
        except Exception as e:
            await self.send_status_embed(f"❌ 關閉失敗：```{e}```", discord.Color.red())

    @commands.command(name="remc")
    async def restart_server(self, ctx):
        if not self.is_process_running():
            await self.send_status_embed("⚠️ 伺服器目前未啟動，將直接啟動。", discord.Color.orange())
            await self.start_server(ctx)
            return
        await self.send_status_embed("🔄 正在重新啟動伺服器...")
        await self.stop_server(ctx)
        await asyncio.sleep(10)
        await self.start_server(ctx)

async def setup(bot):
    print("✅ minecraftserver.py: setup() 執行中")
    await bot.add_cog(MinecraftServerControl(bot))
