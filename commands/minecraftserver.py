import os
import subprocess
import psutil
import asyncio
from datetime import datetime
from discord.ext import commands
from mcrcon import MCRcon
from mcstatus import JavaServer
from config import (
    MINECRAFT_JAR_KEYWORD,
    MINECRAFT_BASE_PATH,
    MINECRAFT_RCON_PORT,
    MINECRAFT_RCON_PASSWORD,
    MINECRAFT_START_BAT
)

class MinecraftServerControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server_jar_keyword = MINECRAFT_JAR_KEYWORD
        self.server_base_path = MINECRAFT_BASE_PATH
        self.rcon_host = "127.0.0.1"
        self.rcon_port = MINECRAFT_RCON_PORT
        self.rcon_password = MINECRAFT_RCON_PASSWORD
        self.last_started = None
        self.last_backup = None

    def is_process_running(self):
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any(self.server_jar_keyword in str(arg) for arg in cmdline):
                    return True
            except:
                continue
        return False

    @commands.command(name="startmc")
    async def start_server(self, ctx):
        if self.is_process_running():
            print("⚠️ Minecraft 已在執行中")
            return False
        try:
            subprocess.Popen(
                os.path.join(self.server_base_path, MINECRAFT_START_BAT),
                cwd=self.server_base_path,
                shell=True
            )
            self.last_started = datetime.now()
            server = JavaServer("127.0.0.1", 25565)
            for _ in range(18):
                try:
                    _ = server.status()
                    print("✅ Minecraft 啟動完成")
                    return True
                except:
                    await asyncio.sleep(5)
            print("⚠️ 伺服器啟動超時")
            return None
        except Exception as e:
            print(f"❌ Minecraft 啟動失敗：{e}")
            return None

    @commands.command(name="stopmc")
    async def stop_server(self, ctx):
        if not self.is_process_running():
            print("⚠️ Minecraft 尚未啟動")
            return False
        try:
            with MCRcon(self.rcon_host, self.rcon_password, self.rcon_port) as mcr:
                mcr.command("say [Discord] 即將關閉伺服器")
                mcr.command("stop")
            print("🛑 Minecraft 關閉成功")
            return True
        except Exception as e:
            print(f"❌ Minecraft 關閉失敗：{e}")
            return None

async def setup(bot):
    await bot.add_cog(MinecraftServerControl(bot))
