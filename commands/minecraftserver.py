import os
import subprocess
import psutil
import asyncio
from datetime import datetime
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
from mcrcon import MCRcon
from mcstatus import JavaServer
from utils.logger import get_logger
from commands.mc_server_config import MinecraftServerProfile, load_servers, get_server

logger = get_logger(__name__)


class MinecraftServerControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.servers: dict[str, MinecraftServerProfile] = {s.id: s for s in load_servers()}
        self.last_started: dict[str, datetime] = {}
        self.last_backup: dict[str, datetime] = {}
        self.delete_delay = 10
        logger.info(f"📋 已載入 {len(self.servers)} 個 Minecraft 伺服器：{list(self.servers.keys())}")

    def list_servers(self) -> list[MinecraftServerProfile]:
        return list(self.servers.values())

    def get_profile(self, server_id: str) -> Optional[MinecraftServerProfile]:
        return self.servers.get(server_id)

    def default_server_id(self) -> Optional[str]:
        servers = self.list_servers()
        return servers[0].id if servers else None

    def _read_pid_file(self, profile: MinecraftServerProfile) -> Optional[int]:
        if os.path.exists(profile.pid_file):
            try:
                with open(profile.pid_file, "r") as f:
                    return int(f.read().strip())
            except Exception as e:
                logger.warning(f"⚠️ 無法讀取 PID 檔案 ({profile.id})：{e}")
        return None

    def _pid_has_java_descendant(self, pid: int) -> bool:
        """檢查 PID 本身或其子孫進程中是否含 java（Minecraft 伺服器本體）"""
        try:
            proc = psutil.Process(pid)
            if proc.name().lower() in ("java.exe", "javaw.exe", "java", "javaw"):
                return True
            for child in proc.children(recursive=True):
                try:
                    if child.name().lower() in ("java.exe", "javaw.exe", "java", "javaw"):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        return False

    def _find_pid_by_cwd(self, profile: MinecraftServerProfile) -> Optional[int]:
        """使用工作目錄比對來尋找正在運行的伺服器進程（必須含 java 子孫）"""
        target = os.path.normpath(os.path.abspath(profile.base_path)).lower()
        for proc in psutil.process_iter(['pid']):
            try:
                cwd = proc.cwd()
                if not cwd or os.path.normpath(os.path.abspath(cwd)).lower() != target:
                    continue
                pid = proc.info['pid']
                if self._pid_has_java_descendant(pid):
                    return pid
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return None

    def _write_pid_file(self, profile: MinecraftServerProfile, pid: int):
        try:
            with open(profile.pid_file, "w") as f:
                f.write(str(pid))
        except Exception as e:
            logger.warning(f"⚠️ 寫入 PID 檔案失敗 ({profile.id})：{e}")

    def _remove_pid_file(self, profile: MinecraftServerProfile):
        try:
            if os.path.exists(profile.pid_file):
                os.remove(profile.pid_file)
        except Exception as e:
            logger.warning(f"⚠️ 移除 PID 檔案失敗 ({profile.id})：{e}")

    def get_pid(self, profile: MinecraftServerProfile) -> Optional[int]:
        # 先從 PID 檔讀取，並驗證進程仍是 Minecraft（含 java 子孫）
        pid = self._read_pid_file(profile)
        if pid is not None and psutil.pid_exists(pid):
            if self._pid_has_java_descendant(pid):
                return pid
            # PID 仍存在但已不是 Minecraft 伺服器（例如遊戲內 /stop 後只剩 cmd.exe 外殼）
            logger.info(f"🧹 [{profile.id}] PID {pid} 不含 Java 子進程，視為已關閉並清除 PID 檔")
            self._remove_pid_file(profile)
        elif pid is not None:
            # PID 已消失
            self._remove_pid_file(profile)

        # Fallback：用 cwd 掃描含 Java 子孫的進程
        pid = self._find_pid_by_cwd(profile)
        if pid is not None:
            logger.info(f"🔍 [{profile.id}] 透過 cwd 找到運行中的進程 PID: {pid}，重建 PID 檔")
            self._write_pid_file(profile, pid)
            return pid
        return None

    def is_process_running(self, profile: MinecraftServerProfile) -> bool:
        return self.get_pid(profile) is not None

    async def send_msg(self, ctx, content: str):
        return await ctx.send(content, delete_after=self.delete_delay)

    async def _resolve_server(self, ctx, server_id: Optional[str]) -> Optional[MinecraftServerProfile]:
        if not self.servers:
            await self.send_msg(ctx, "❌ 尚未設定任何 Minecraft 伺服器，請檢查 `data/minecraft_servers.json`")
            return None
        sid = server_id or self.default_server_id()
        profile = self.get_profile(sid)
        if not profile:
            available = ", ".join(self.servers.keys())
            await self.send_msg(ctx, f"❌ 找不到伺服器 `{sid}`，可用：{available}")
            return None
        return profile

    @commands.hybrid_command(name="startmc")
    @app_commands.describe(server_id="要啟動的伺服器 ID（省略則使用第一個）")
    async def start_server(self, ctx, server_id: Optional[str] = None) -> Optional[bool]:
        profile = await self._resolve_server(ctx, server_id)
        if not profile:
            return None

        if self.is_process_running(profile):
            logger.warning(f"⚠️ [{profile.id}] 已在執行中")
            await self.send_msg(ctx, f"⚠️ {profile.name} 已在執行中")
            return False

        try:
            proc = subprocess.Popen(
                profile.start_bat_path,
                cwd=profile.base_path,
                shell=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
            # 立刻寫入 PID，避免啟動過程中因逾時而丟失
            self._write_pid_file(profile, proc.pid)
            self.last_started[profile.id] = datetime.now()
            logger.info(f"🚀 [{profile.id}] 啟動中，PID: {proc.pid}")
            await self.send_msg(ctx, f"🚀 {profile.name} 啟動中...")

            server = JavaServer(profile.host, profile.game_port)
            interval = 5
            max_attempts = max(1, profile.startup_timeout // interval)
            for i in range(max_attempts):
                try:
                    server.status()
                    with MCRcon(profile.host, profile.rcon_password, profile.rcon_port) as mcr:
                        mcr.command("list")
                    logger.info(f"✅ [{profile.id}] 啟動完成（已連線 RCON）")
                    await self.send_msg(ctx, f"✅ {profile.name} 啟動完成")

                    if profile.auto_backup and self.bot and hasattr(self.bot, "backup_task"):
                        self.bot.backup_task.start()
                    return True
                except Exception as e:
                    logger.debug(f"[{profile.id}] 等待中 ({i+1}/{max_attempts})... {e}")
                    await asyncio.sleep(interval)

            logger.warning(f"⚠️ [{profile.id}] RCON 連線逾時（{profile.startup_timeout}s），但進程仍在運行")
            await self.send_msg(
                ctx,
                f"⚠️ {profile.name} RCON 連線逾時（可能仍在載入中），進程 PID {proc.pid} 已追蹤，可稍後手動確認或重試關閉"
            )
            if profile.auto_backup and self.bot and hasattr(self.bot, "backup_task"):
                self.bot.backup_task.start()
            return True

        except Exception as e:
            logger.error(f"❌ [{profile.id}] 啟動失敗：{e.__class__.__name__} - {e}")
            await self.send_msg(ctx, f"❌ {profile.name} 啟動失敗：{e}")
            return None

    @commands.hybrid_command(name="stopmc")
    @app_commands.describe(server_id="要關閉的伺服器 ID（省略則使用第一個）")
    async def stop_server(self, ctx, server_id: Optional[str] = None) -> Optional[bool]:
        profile = await self._resolve_server(ctx, server_id)
        if not profile:
            return None

        logger.info(f"🚦 stopmc 指令收到 [{profile.id}]")
        pid = self.get_pid(profile)
        if not pid or not psutil.pid_exists(pid):
            logger.warning(f"⚠️ [{profile.id}] 尚未啟動")
            await self.send_msg(ctx, f"⚠️ {profile.name} 尚未啟動")
            return False

        try:
            with MCRcon(profile.host, profile.rcon_password, profile.rcon_port) as mcr:
                mcr.command("say [Discord] 即將關閉伺服器")
                mcr.command("save-all")
                mcr.command("stop")
            logger.info(f"📴 [{profile.id}] RCON stop 指令已送出")
            await self.send_msg(ctx, f"📴 已發送關閉指令給 {profile.name}")

            for _ in range(12):
                if not psutil.pid_exists(pid):
                    await asyncio.sleep(1)
                    if not psutil.pid_exists(pid):
                        logger.info(f"🛑 [{profile.id}] 已成功關閉")
                        await self.send_msg(ctx, f"🛑 {profile.name} 已成功關閉")
                        if os.path.exists(profile.pid_file):
                            os.remove(profile.pid_file)
                        if self.bot and hasattr(self.bot, "backup_task"):
                            asyncio.create_task(self._stop_backup_if_all_idle())
                        return True
                await asyncio.sleep(5)

            logger.warning(f"⚠️ [{profile.id}] stop 指令送出後仍未關閉，準備強制終止")
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                proc.wait(timeout=10)
                logger.info(f"⚠️ [{profile.id}] 強制終止")
                await self.send_msg(ctx, f"⚠️ 已強制關閉 {profile.name}")
                if os.path.exists(profile.pid_file):
                    os.remove(profile.pid_file)
                if self.bot and hasattr(self.bot, "backup_task"):
                    asyncio.create_task(self._stop_backup_if_all_idle())
                return True
            except Exception as e:
                logger.error(f"❌ [{profile.id}] 強制關閉失敗：{e}")
                await self.send_msg(ctx, f"❌ {profile.name} 關閉失敗：{e}")
                return None

        except Exception as e:
            logger.error(f"❌ [{profile.id}] 關閉失敗：{e.__class__.__name__} - {e}")
            await self.send_msg(ctx, f"❌ {profile.name} 關閉失敗：{e}")
            return None

    async def _stop_backup_if_all_idle(self):
        await asyncio.sleep(300)
        if any(self.is_process_running(p) for p in self.servers.values()):
            return
        if hasattr(self.bot, "backup_task"):
            self.bot.backup_task.stop()
            logger.info("📦 所有 Minecraft 伺服器均已關閉，自動備份任務已停止")


async def setup(bot):
    await bot.add_cog(MinecraftServerControl(bot))
