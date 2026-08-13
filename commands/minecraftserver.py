import os
import re
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
from utils.logger import clear_channel_log, get_logger
from utils.mc_log import detect_clean_shutdown, tail_lines
from commands.mc_server_config import MinecraftServerProfile, load_servers, get_server

logger = get_logger(__name__, channel="minecraft")

# 用來精簡 console log 行:[時間] [執行緒/等級] [logger/]: 訊息
_LOG_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]*)\]\s*\[(?P<thread>[^\]]*)\]\s*\[(?P<logger>[^\]]*)\]:\s*(?P<msg>.*)$"
)
_TIME_RE = re.compile(r"\d{2}:\d{2}:\d{2}")


class MinecraftServerControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.servers: dict[str, MinecraftServerProfile] = {}
        self.last_started: dict[str, datetime] = {}
        self.last_backup: dict[str, datetime] = {}
        self.delete_delay = 10
        self.reload_servers()

    def reload_servers(self) -> None:
        """重新讀取 data/minecraft_servers.json,可在動態新增/移除/啟用後呼叫"""
        self.servers = {s.id: s for s in load_servers()}
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

    def _kill_process_tree(self, pid: int) -> None:
        """終止整棵進程樹(先子後父)。

        Windows 的 TerminateProcess 只殺單一進程,若只 terminate 外殼 cmd.exe,
        底下的 java 會變成孤兒繼續佔用世界檔案 — 所以一定要連子孫一起處理。
        """
        try:
            parent = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return

        children = []
        try:
            children = parent.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        for proc in children + [parent]:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        gone, alive = psutil.wait_procs(children + [parent], timeout=10)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _cleanup_shell(self, profile: MinecraftServerProfile, pid: int) -> None:
        """清掉 java 退出後殘留的 cmd 外殼。

        各 server 的 run.bat 結尾都有 `pause`,java 正常關閉後 cmd.exe 會卡在
        「請按任意鍵繼續」不會自行退出。這是**正常收尾**,不是強制關閉,
        所以只寫 log 不對使用者示警。
        """
        if not psutil.pid_exists(pid):
            return
        try:
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        logger.info(f"🧹 [{profile.id}] 清理殘留的啟動外殼 {name} (PID {pid})")
        self._kill_process_tree(pid)

    def _shutdown_evidence(self, profile: MinecraftServerProfile) -> tuple[bool, Optional[str]]:
        """從 server 自己的 console log 佐證本次關閉是否完成存檔。"""
        try:
            return detect_clean_shutdown(profile.console_log_path)
        except Exception as e:
            logger.warning(f"⚠️ [{profile.id}] 讀取 console log 失敗:{e}")
            return False, None

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

    async def send_msg(self, ctx, content: str, delete_after: Optional[int] = None):
        return await ctx.send(
            content,
            delete_after=self.delete_delay if delete_after is None else delete_after,
        )

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

    async def _resolve_server_prefer_running(
        self, ctx, server_id: Optional[str]
    ) -> Optional[MinecraftServerProfile]:
        """指定 id 時照指定的來；沒指定則優先挑正在執行的那台。

        三台伺服器共用 25565/25575，實際上同時只會跑一台，
        所以「沒指定就用執行中的」比「用清單第一台」更符合直覺。
        """
        if server_id:
            return await self._resolve_server(ctx, server_id)
        for profile in self.list_servers():
            if self.is_process_running(profile):
                return profile
        return await self._resolve_server(ctx, None)

    def can_start(self, profile: MinecraftServerProfile, user_id: Optional[int]) -> bool:
        """這個使用者現在能不能啟動這台伺服器（開放時段 / 授權使用者）。

        面板按鈕必須用 `interaction.user.id` 來問，不能用 `ctx.author`（那會是 bot 自己）。
        """
        return profile.window.can_start(user_id)

    def start_deny_message(self, profile: MinecraftServerProfile) -> str:
        return profile.window.deny_message(profile.name)

    @commands.hybrid_command(name="startmc")
    @app_commands.describe(server_id="要啟動的伺服器 ID（省略則使用第一個）")
    async def start_server(self, ctx, server_id: Optional[str] = None) -> Optional[bool]:
        profile = await self._resolve_server(ctx, server_id)
        if not profile:
            return None

        if not self.can_start(profile, ctx.author.id):
            logger.warning(f"⛔ [{profile.id}] 使用者 {ctx.author.id} 在非開放時段嘗試啟動，已拒絕")
            await self.send_msg(ctx, self.start_deny_message(profile), delete_after=30)
            return False

        return await self.do_start(ctx, profile)

    async def do_start(self, ctx, profile: MinecraftServerProfile) -> Optional[bool]:
        """實際啟動流程（不含時段檢查），供指令與控制面板共用。"""
        if self.is_process_running(profile):
            logger.warning(f"⚠️ [{profile.id}] 已在執行中")
            await self.send_msg(ctx, f"⚠️ {profile.name} 已在執行中")
            return False

        # 每次啟動清空 minecraft.log,讓本次運行的紀錄純淨易讀
        clear_channel_log("minecraft")
        logger.info(f"🆕 [{profile.id}] 啟動流程開始,minecraft.log 已重置")

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
        # get_pid() 已確認過進程存在且含 java 子孫，這裡不需要再驗一次 pid_exists
        pid = self.get_pid(profile)
        if not pid:
            logger.warning(f"⚠️ [{profile.id}] 尚未啟動")
            await self.send_msg(ctx, f"⚠️ {profile.name} 尚未啟動")
            return False

        try:
            # RCON 送不出去不能直接放棄：伺服器可能已經是「存完檔但 JVM 卡住不退出」的殘留
            # 進程（RCON Listener 早就停了），那正是最需要被清掉的情況。
            rcon_ok = True
            try:
                # 先在遊戲內倒數，讓線上玩家有時間收尾（節點由每台伺服器各自設定）
                await self._broadcast_countdown(ctx, profile)

                with MCRcon(profile.host, profile.rcon_password, profile.rcon_port) as mcr:
                    # flush 會等到區塊真的寫入磁碟才回應，比單純 save-all 更保險
                    save_reply = mcr.command("save-all flush")
                    logger.info(f"💾 [{profile.id}] save-all flush 回應：{save_reply.strip() or '(無回應)'}")
                    mcr.command("stop")
                logger.info(f"📴 [{profile.id}] RCON stop 指令已送出")
                await self.send_msg(ctx, f"📴 已發送關閉指令給 {profile.name}，等待存檔完成...")
            except Exception as e:
                rcon_ok = False
                logger.warning(
                    f"⚠️ [{profile.id}] RCON 無法送出關閉指令（{e.__class__.__name__}: {e}），"
                    f"改為直接終止進程"
                )
                await self.send_msg(
                    ctx, f"⚠️ {profile.name} 的 RCON 沒有回應，改為直接終止進程..."
                )

            # 判斷「關完了沒」要看 java 有沒有退出，不能看 pid_exists：
            # run.bat 結尾的 pause 會讓外殼 cmd.exe 一直活著，那不代表伺服器還在跑。
            # RCON 送不出去時 java 不會自己退出，只做短暫確認就進強制終止，不空等。
            interval = 2
            timeout = profile.shutdown_timeout if rcon_ok else 6
            for _ in range(max(1, timeout // interval)):
                if not self._pid_has_java_descendant(pid):
                    return await self._finish_graceful_stop(ctx, profile, pid)
                await asyncio.sleep(interval)

            # java 還活著。先看 log 佐證存檔有沒有完成，再決定怎麼說
            saved, evidence = self._shutdown_evidence(profile)
            logger.warning(
                f"⚠️ [{profile.id}] 等待 {timeout}s 後 Java 仍未退出"
                f"（RCON {'正常' if rcon_ok else '無回應'}），"
                f"存檔佐證={'有' if saved else '無'}，準備強制終止"
            )
            try:
                self._kill_process_tree(pid)
            except Exception as e:
                logger.error(f"❌ [{profile.id}] 強制關閉失敗：{e}")
                await self.send_msg(ctx, f"❌ {profile.name} 關閉失敗：{e}")
                return None

            self._remove_pid_file(profile)
            if self.bot and hasattr(self.bot, "backup_task"):
                asyncio.create_task(self._stop_backup_if_all_idle())

            if saved:
                # 大型整合包常見：世界已存完，但有 mod 的執行緒卡住不讓 JVM 退出
                logger.info(f"🛑 [{profile.id}] 存檔已完成，強制結束殘留執行緒｜佐證：{evidence}")
                await self.send_msg(
                    ctx,
                    f"🛑 {profile.name} 已關閉（世界存檔已完成）\n"
                    f"　└ 伺服器存檔後有執行緒卡住未退出，已強制結束，**世界資料是安全的**"
                )
            else:
                logger.warning(f"⚠️ [{profile.id}] 強制終止，且無法從 console log 佐證存檔完成")
                await self.send_msg(
                    ctx,
                    f"⚠️ 已強制關閉 {profile.name}\n"
                    f"　└ **無法從 console log 佐證存檔完成**，建議用 `/mcconsole {profile.id}` 檢查最後輸出"
                )
            return True

        except Exception as e:
            logger.error(f"❌ [{profile.id}] 關閉失敗：{e.__class__.__name__} - {e}")
            await self.send_msg(ctx, f"❌ {profile.name} 關閉失敗：{e}")
            return None

    async def _finish_graceful_stop(self, ctx, profile: MinecraftServerProfile, pid: int) -> bool:
        """Java 已自行退出後的收尾：佐證存檔、清外殼、清 PID 檔。"""
        saved, evidence = self._shutdown_evidence(profile)
        self._cleanup_shell(profile, pid)
        self._remove_pid_file(profile)
        if self.bot and hasattr(self.bot, "backup_task"):
            asyncio.create_task(self._stop_backup_if_all_idle())

        if saved:
            logger.info(f"🛑 [{profile.id}] 已安全關閉｜存檔佐證：{evidence}")
            await self.send_msg(ctx, f"🛑 {profile.name} 已安全關閉（世界存檔完成）")
        else:
            logger.warning(f"⚠️ [{profile.id}] 進程已結束，但 console log 沒有存檔完成標記")
            await self.send_msg(
                ctx,
                f"🛑 {profile.name} 進程已結束\n"
                f"　└ 但 console log 沒有出現存檔完成標記，可用 `/mcconsole {profile.id}` 檢查"
            )
        return True

    async def _send_progress(self, ctx, content: str):
        """送出一則之後會被編輯的進度訊息；失敗回 None（不影響主流程）。"""
        try:
            return await ctx.send(content)
        except Exception as e:
            logger.warning(f"⚠️ 送出進度訊息失敗：{e}")
            return None

    async def _update_progress(self, msg, content: str, delete_after: Optional[int] = None):
        if msg is None:
            return
        try:
            await msg.edit(content=content)
            if delete_after is not None:
                await msg.delete(delay=delete_after)
        except Exception:
            pass

    async def _broadcast_countdown(self, ctx, profile: MinecraftServerProfile) -> None:
        """關閉前在遊戲內倒數廣播；節點由 profile.shutdown_countdown 設定。

        節點清單為空 = 不倒數，直接關閉（維持舊行為）。
        """
        steps = profile.countdown_steps
        if not steps:
            logger.info(f"[{profile.id}] 未設定倒數節點，直接進入關閉流程")
            return

        total = steps[0]
        logger.info(f"⏳ [{profile.id}] 關閉倒數開始，共 {total} 秒，節點：{steps}")
        progress = await self._send_progress(
            ctx, f"⏳ {profile.name} 將在 {total} 秒後關閉（遊戲內倒數中）"
        )

        remaining = total
        for sec in steps:
            wait = remaining - sec
            if wait > 0:
                await asyncio.sleep(wait)

            text = f"伺服器將在 {sec} 秒後關閉" if sec >= 10 else f"關閉倒數 {sec}"
            try:
                await asyncio.to_thread(self._rcon_command, profile, f"say [關機] {text}")
            except Exception as e:
                logger.warning(f"⚠️ [{profile.id}] 倒數廣播失敗（{sec}s）：{e}")
                if sec == total:
                    # 第一則就送不出去 = RCON 大概沒在聽，繼續空等整個倒數沒有意義
                    logger.warning(f"⚠️ [{profile.id}] RCON 無回應，跳過倒數直接關閉")
                    await self._update_progress(
                        progress, f"⚠️ {profile.name} RCON 無回應，跳過倒數直接關閉", delete_after=30
                    )
                    return
            await self._update_progress(progress, f"⏳ {profile.name} 關閉倒數 {sec} 秒...")
            remaining = sec

        if remaining > 0:
            await asyncio.sleep(remaining)
        logger.info(f"⏳ [{profile.id}] 倒數結束，開始存檔並關閉")
        await self._update_progress(
            progress, f"📴 {profile.name} 倒數結束，開始存檔並關閉...", delete_after=30
        )

    @commands.hybrid_command(name="mccmd")
    @commands.is_owner()
    @app_commands.describe(
        command="要送到伺服器的指令（不用加斜線），例如 list、save-all、time set day",
        server_id="目標伺服器 ID（省略則使用執行中的那台）",
    )
    async def mc_command(self, ctx, command: str, server_id: Optional[str] = None):
        """透過 RCON 對 Minecraft 伺服器送指令，等同在 server console 打字。"""
        profile = await self._resolve_server_prefer_running(ctx, server_id)
        if not profile:
            return

        command = command.strip().lstrip("/")
        if not command:
            await self.send_msg(ctx, "❌ 指令不可為空")
            return

        if not self.is_process_running(profile):
            await self.send_msg(ctx, f"⚠️ {profile.name} 尚未啟動，無法送指令")
            return

        if command.split()[0].lower() == "stop":
            await self.send_msg(
                ctx,
                f"⚠️ 請改用 `/stopmc {profile.id}` 關閉伺服器"
                "（會等待存檔完成、確認結果並清理殘留進程）"
            )
            return

        try:
            reply = await asyncio.to_thread(self._rcon_command, profile, command)
        except Exception as e:
            logger.error(f"❌ [{profile.id}] RCON 指令失敗（{command}）：{e.__class__.__name__} - {e}")
            await self.send_msg(ctx, f"❌ 指令送出失敗：{e}")
            return

        logger.info(f"⌨️ [{profile.id}] {ctx.author} 送出指令：{command}")
        reply = (reply or "").strip() or "（無回應）"
        body = self._as_code_block(reply, limit=1700)
        await self.send_msg(ctx, f"⌨️ `{profile.name}` ▸ `{command}`\n{body}", delete_after=60)

    @commands.hybrid_command(name="mcconsole")
    @commands.is_owner()
    @app_commands.describe(
        server_id="目標伺服器 ID（省略則使用執行中的那台）",
        lines="要顯示的行數（1-100，預設 25）",
        raw="True 顯示原始 log 行，False（預設）精簡掉時間戳與 logger 名稱",
    )
    async def mc_console(
        self,
        ctx,
        server_id: Optional[str] = None,
        lines: int = 25,
        raw: bool = False,
    ):
        """顯示 Minecraft 伺服器 console（logs/latest.log）的最後幾行。"""
        profile = await self._resolve_server_prefer_running(ctx, server_id)
        if not profile:
            return

        lines = max(1, min(100, lines))
        log_path = profile.console_log_path
        if not os.path.exists(log_path):
            await self.send_msg(ctx, f"❌ 找不到 console log：`{log_path}`")
            return

        try:
            log_lines = await asyncio.to_thread(tail_lines, log_path, lines)
        except Exception as e:
            logger.error(f"❌ [{profile.id}] 讀取 console log 失敗：{e}")
            await self.send_msg(ctx, f"❌ 讀取 console log 失敗：{e}")
            return

        if not log_lines:
            await self.send_msg(ctx, f"⚠️ {profile.name} 的 console log 是空的")
            return

        if not raw:
            log_lines = [self._simplify_log_line(ln) for ln in log_lines]

        state = "🟢 執行中" if self.is_process_running(profile) else "⚫ 未執行"
        header = f"📜 `{profile.name}` {state}｜console 最後 {len(log_lines)} 行"
        body = self._as_code_block("\n".join(log_lines), limit=1800)
        await self.send_msg(ctx, f"{header}\n{body}", delete_after=120)

    @staticmethod
    def _rcon_command(profile: MinecraftServerProfile, command: str) -> str:
        """同步送出一條 RCON 指令（給 asyncio.to_thread 用）。"""
        with MCRcon(profile.host, profile.rcon_password, profile.rcon_port) as mcr:
            return mcr.command(command)

    @staticmethod
    def _simplify_log_line(line: str) -> str:
        """把 `[13八月2026 04:54:39.472] [Server thread/INFO] [net.minecraft...]: 訊息`
        精簡成 `[04:54:39] [INFO] 訊息`；格式不符就原樣回傳。"""
        m = _LOG_LINE_RE.match(line)
        if not m:
            return line
        ts = _TIME_RE.search(m.group("ts"))
        level = m.group("thread").rsplit("/", 1)[-1]
        prefix = f"[{ts.group(0)}] " if ts else ""
        return f"{prefix}[{level}] {m.group('msg')}"

    @staticmethod
    def _as_code_block(text: str, limit: int) -> str:
        """包成 code block；過長時從尾端保留（最新的內容比較重要）。"""
        if len(text) > limit:
            text = "…（已截斷前段）\n" + text[-limit:]
        return f"```\n{text}\n```"

    async def _stop_backup_if_all_idle(self):
        await asyncio.sleep(300)
        if any(self.is_process_running(p) for p in self.servers.values()):
            return
        if hasattr(self.bot, "backup_task"):
            self.bot.backup_task.stop()
            logger.info("📦 所有 Minecraft 伺服器均已關閉，自動備份任務已停止")


async def setup(bot):
    await bot.add_cog(MinecraftServerControl(bot))
