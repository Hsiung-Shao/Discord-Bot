import os
import subprocess
import psutil
import asyncio
import discord
import shutil
import tempfile
import datetime
from discord.ext import commands, tasks
from mcrcon import MCRcon
from mcstatus import JavaServer
from config import (
    MINECRAFT_JAR_KEYWORD,
    MINECRAFT_BASE_PATH,
    MINECRAFT_RCON_PORT,
    MINECRAFT_RCON_PASSWORD,
    MINECRAFT_STATUS_THREAD_ID,
    MINECRAFT_START_BAT,
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
        self.backup_path = os.path.join("backups", "MC")
        self.world_path = os.path.join(self.server_base_path, "world")

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
            await self.send_status_embed("✅ Minecraft 啟動指令已送出，正在等待伺服器上線...")

            # 等待伺服器啟動（最多 90 秒）
            server = JavaServer("127.0.0.1", 25565)
            for _ in range(18):  # 每 5 秒檢查一次
                try:
                    status = server.status()
                    await self.send_status_embed(
                        f"🎉 Minecraft 伺服器啟動完成！\n👥 玩家上限：{status.players.max}\n📃 MOTD：{status.description}",
                        discord.Color.green()
                    )
                    # ✅ 啟動成功後才啟動備份任務（防止卡住）
                    if not self.auto_backup_task.is_running():
                        self.auto_backup_task.start()
                    return
                except Exception:
                    await asyncio.sleep(5)

            await self.send_status_embed("⚠️ 無法確認伺服器是否成功啟動（連線逾時）", discord.Color.orange())

        except Exception as e:
            await self.send_status_embed(f"❌ 啟動失敗：```{e}```", discord.Color.red())


    @commands.command(name="stopmc")
    async def stop_server(self, ctx):
        try:
            with MCRcon(self.rcon_host, self.rcon_password, self.rcon_port) as mcr:
                mcr.command("say [Discord] 即將關閉伺服器")
                mcr.command("stop")

            await self.send_status_embed("🛑 關閉指令已送出，正在等待伺服器關閉...", discord.Color.orange())

            # 等待最多 60 秒確認關閉
            for _ in range(20):  # 每 3 秒檢查一次，最多 60 秒
                if not self.is_process_running():
                    await self.send_status_embed("✅ Minecraft 伺服器已成功關閉。", discord.Color.red())
                    if self.auto_backup_task.is_running():
                        self.auto_backup_task.cancel()
                    return
                await asyncio.sleep(3)

            await self.send_status_embed("⚠️ 關閉指令已送出，但伺服器仍未完全關閉（逾時）", discord.Color.orange())

        except Exception as e:
            await self.send_status_embed(f"❌ 關閉失敗：```{e}```", discord.Color.red())

    @commands.command(name="statusmc")
    async def status_mc(self, ctx):
        server = JavaServer("127.0.0.1", 25565)
        try:
            status = server.status()
            embed = discord.Embed(
                title="🟢 Minecraft 伺服器狀態",
                description="伺服器目前正在執行中。",
                color=discord.Color.green()
            )
            embed.add_field(name="IP:", value=f"26.82.236.63 | 125.228.138.70", inline=False)
            embed.add_field(name="玩家上限", value=f"{status.players.online} / {status.players.max}", inline=True)
            embed.add_field(name="MOTD", value=str(status.description), inline=False)
        except Exception:
            embed = discord.Embed(
                title="🔴 Minecraft 伺服器狀態",
                description="伺服器目前未在執行或無法連線。",
                color=discord.Color.red()
            )

        # 若由控制面板觸發則回傳給面板更新，不另發訊息
        if hasattr(ctx, "update_embed"):
            await ctx.update_embed(embed)
        else:
            await ctx.send(embed=embed)

    def backup_server_world(self):
        now = datetime.datetime.now()
        timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        zip_name = f"backup_{timestamp}.zip"
        os.makedirs(self.backup_path, exist_ok=True)
        zip_path = os.path.join(self.backup_path, zip_name)

        try:
            temp_dir = tempfile.mkdtemp()
            temp_world = os.path.join(temp_dir, "world")

            def ignore_session_lock(src, names):
                return ['session.lock'] if 'session.lock' in names else []

            shutil.copytree(self.world_path, temp_world, ignore=ignore_session_lock)
            shutil.make_archive(zip_path.replace(".zip", ""), 'zip', temp_world)
            shutil.rmtree(temp_dir)

            self.cleanup_old_backups()
            return True, zip_path
        except Exception as e:
            return False, str(e)

    def cleanup_old_backups(self, hours=36):
        cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
        for file in os.listdir(self.backup_path):
            if file.endswith(".zip"):
                full_path = os.path.join(self.backup_path, file)
                try:
                    modified = datetime.datetime.fromtimestamp(os.path.getmtime(full_path))
                    if modified < cutoff:
                        os.remove(full_path)
                        print(f"🧹 已刪除過期備份：{file}")
                except Exception as e:
                    print(f"⚠️ 刪除備份錯誤：{e}")

    @tasks.loop(hours=1)
    async def auto_backup_task(self):
        ok, result = await asyncio.to_thread(self.backup_server_world)
        if ok:
            print(f"[備份] ✅ 成功：{result}")
        else:
            print(f"[備份] ❌ 失敗：{result}")

    @auto_backup_task.before_loop
    async def before_auto_backup(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(MinecraftServerControl(bot))
