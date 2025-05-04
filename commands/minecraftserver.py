import os
import subprocess
import psutil
import asyncio
from datetime import datetime
from discord.ext import commands
from mcrcon import MCRcon
from mcstatus import JavaServer
from utils.logger import get_logger
from config import (
    MINECRAFT_JAR_KEYWORD,
    MINECRAFT_BASE_PATH,
    MINECRAFT_RCON_PORT,
    MINECRAFT_RCON_PASSWORD,
    MINECRAFT_START_BAT
)
logger = get_logger(__name__)
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
                name = proc.info.get('name') or ''
                logger.debug(f"[檢查中] PID={proc.pid}, name={name}, cmdline={cmdline}")
                if (
                    "java" in name.lower()
                    and any("server" in arg or ".jar" in arg for arg in cmdline)
                    and any(self.server_jar_keyword in arg for arg in cmdline)
                ):
                    logger.debug(f"✅ 偵測到 Minecraft 進程：{cmdline}")
                    return True
            except Exception as e:
                logger.warning(f"⚠️ 無法檢查進程：{e}")
                continue
        return False



    @commands.command(name="startmc")
    async def start_server(self, ctx):
        if self.is_process_running():
            print("⚠️ Minecraft 已在執行中")
            logger.warning("⚠️ Minecraft 已在執行中")
            return False
        try:
            subprocess.Popen(
                os.path.join(self.server_base_path, MINECRAFT_START_BAT),
                cwd=self.server_base_path,
                shell=True
            )
            self.last_started = datetime.now()
            logger.info("🚀 啟動 Minecraft Server 中...")
            server = JavaServer("127.0.0.1", 25565)
            for _ in range(18):
                try:
                    _ = server.status()
                    logger.info("✅ Minecraft 啟動完成")
                    return True
                except:
                    await asyncio.sleep(5)
            logger.warning("⚠️ 伺服器啟動超時")
            return None
        except Exception as e:
            logger.error(f"❌ Minecraft 啟動失敗：{e.__class__.__name__} - {e}")
            return None

    @commands.command(name="stopmc")
    async def stop_server(self, ctx):
        if not self.is_process_running():
            logger.warning("⚠️ Minecraft 尚未啟動")
            return False
        try:
            with MCRcon(self.rcon_host, self.rcon_password, self.rcon_port) as mcr:
                response = mcr.command("say [Discord] 即將關閉伺服器")
                logger.info(f"📣 RCON response: {response}")
                mcr.command("stop")

            # 等待進程關閉
            for _ in range(12):  # 最多等 60 秒
                if not self.is_process_running():
                    logger.info("🛑 Minecraft 已成功關閉")
                    return True
                await asyncio.sleep(5)

            logger.warning("⚠️ stop 指令送出但伺服器仍未關閉")
            return None
        except Exception as e:
            logger.error(f"❌ Minecraft 關閉失敗：{e.__class__.__name__} - {e}")
            return None

async def setup(bot):
    await bot.add_cog(MinecraftServerControl(bot))
