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
        self.server_base_path = MINECRAFT_BASE_PATH
        self.rcon_host = "127.0.0.1"
        self.rcon_port = MINECRAFT_RCON_PORT
        self.rcon_password = MINECRAFT_RCON_PASSWORD
        self.start_bat = os.path.join(self.server_base_path, MINECRAFT_START_BAT)
        self.pid_file = os.path.join(self.server_base_path, "server.pid")
        self.last_started = None
        self.delete_delay = 10  # 所有訊息預設刪除秒數

    def get_pid(self):
        if os.path.exists(self.pid_file):
            try:
                with open(self.pid_file, "r") as f:
                    return int(f.read().strip())
            except Exception as e:
                logger.warning(f"⚠️ 無法讀取 PID 檔案: {e}")
        return None

    def is_process_running(self):
        pid = self.get_pid()
        return pid is not None and psutil.pid_exists(pid)

    async def send_msg(self, ctx, content):
        return await ctx.send(content, delete_after=self.delete_delay)

    @commands.command(name="startmc")
    async def start_server(self, ctx):
        if self.is_process_running():
            logger.warning("⚠️ Minecraft 已在執行中")
            await self.send_msg(ctx, "⚠️ Minecraft 已在執行中")
            return

        try:
            proc = subprocess.Popen(
                self.start_bat,
                cwd=self.server_base_path,
                shell=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP  # Windows only
            )
            self.last_started = datetime.now()
            logger.info(f"🚀 啟動 Minecraft 中，PID: {proc.pid}")
            await self.send_msg(ctx, "🚀 Minecraft 啟動中...")

            server = JavaServer("127.0.0.1", 25565)
            for i in range(18):  # 最多等 90 秒
                try:
                    status = server.status()
                    with MCRcon(self.rcon_host, self.rcon_password, self.rcon_port) as mcr:
                        players = mcr.command("list")
                    logger.info("✅ Minecraft 啟動完成（已連線 RCON）")
                    with open(self.pid_file, "w") as f:
                        f.write(str(proc.pid))
                    await self.send_msg(ctx, "✅ Minecraft 啟動完成")

                    if self.bot and hasattr(self.bot, "backup_task"):
                        self.bot.backup_task.start()

                    return
                except Exception as e:
                    logger.debug(f"等待中 ({i+1}/18)... {e}")
                    await asyncio.sleep(5)

            logger.warning("⚠️ Minecraft 啟動逾時，未偵測到 RCON")
            await self.send_msg(ctx, "⚠️ Minecraft 啟動逾時，請手動確認伺服器是否已啟動")

        except Exception as e:
            logger.error(f"❌ Minecraft 啟動失敗：{e.__class__.__name__} - {e}")
            await self.send_msg(ctx, f"❌ Minecraft 啟動失敗：{e}")

    @commands.command(name="stopmc")
    async def stop_server(self, ctx):
        logger.info("🚦 stopmc 指令收到")
        pid = self.get_pid()
        if not pid or not psutil.pid_exists(pid):
            logger.warning("⚠️ Minecraft 尚未啟動")
            await self.send_msg(ctx, "⚠️ Minecraft 尚未啟動")
            return

        try:
            with MCRcon(self.rcon_host, self.rcon_password, self.rcon_port) as mcr:
                mcr.command("say [Discord] 即將關閉伺服器")
                mcr.command("save-all")
                mcr.command("stop")
            logger.info("📴 RCON stop 指令已送出")
            await self.send_msg(ctx, "📴 已發送關閉指令給 Minecraft 伺服器")

            for _ in range(12):
                if not psutil.pid_exists(pid):
                    await asyncio.sleep(1)
                    if not psutil.pid_exists(pid):
                        logger.info("🛑 Minecraft 已成功關閉")
                        await self.send_msg(ctx, "🛑 Minecraft 已成功關閉")
                        if os.path.exists(self.pid_file):
                            os.remove(self.pid_file)

                        if self.bot and hasattr(self.bot, "backup_task"):
                            asyncio.create_task(self._stop_backup_after_delay())

                        return
                await asyncio.sleep(5)

            logger.warning("⚠️ stop 指令送出後仍未關閉，準備強制終止")
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                proc.wait(timeout=10)
                logger.info("⚠️ Minecraft 強制終止")
                await self.send_msg(ctx, "⚠️ 已強制關閉 Minecraft 伺服器")
                if os.path.exists(self.pid_file):
                    os.remove(self.pid_file)

                if self.bot and hasattr(self.bot, "backup_task"):
                    asyncio.create_task(self._stop_backup_after_delay())

                return
            except Exception as e:
                logger.error(f"❌ 強制關閉失敗: {e}")
                await self.send_msg(ctx, f"❌ Minecraft 關閉失敗：{e}")
                return

        except Exception as e:
            logger.error(f"❌ Minecraft 關閉失敗：{e.__class__.__name__} - {e}")
            await self.send_msg(ctx, f"❌ Minecraft 關閉失敗：{e}")

    async def _stop_backup_after_delay(self):
        await asyncio.sleep(300)
        if hasattr(self.bot, "backup_task"):
            self.bot.backup_task.stop()
            logger.info("📦 自動備份任務已關閉")

async def setup(bot):
    await bot.add_cog(MinecraftServerControl(bot))