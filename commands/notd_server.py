import os
import subprocess
import asyncio
import psutil
from datetime import datetime
from discord.ext import commands
from core.start_window import StartWindow
from utils.logger import clear_channel_log, get_logger
from config import NOTD_DIR, NOTD_BAT, NOTD_KEYWORD, NOTD_ALLOWED_USER_ID

logger = get_logger(__name__, channel="notd")


class NotdServerControl(commands.Cog):
    """Night of the Dead 專用伺服器控制（啟動 / 關閉 / 進程偵測）。

    本機以 .bat 啟動 LFServer.exe;該伺服器沒有 RCON / telnet console,
    因此關閉只能終止進程(先 terminate,逾時再 kill)。
    """

    def __init__(self, bot):
        self.bot = bot
        self.base_path = NOTD_DIR
        self.bat_file = NOTD_BAT
        self.keyword = NOTD_KEYWORD
        self.last_started = None
        self.delete_delay = 10
        # 啟動權限:授權使用者不限時段;其他人僅限開放時段(邏輯與 Minecraft 共用)
        self.allowed_user_id = NOTD_ALLOWED_USER_ID
        self.start_window = StartWindow(
            enabled=False,  # 不限時段;要恢復限制改回 True 即可
            from_hour=21,   # 開放啟動時段起點(台北時間,含)
            to_hour=5,      # 開放啟動時段終點(台北時間,不含)
            allowed_user_ids=[NOTD_ALLOWED_USER_ID],
        )

    def is_process_running(self) -> bool:
        for proc in psutil.process_iter(['name']):
            try:
                if proc.info['name'] and self.keyword in proc.info['name']:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return False

    def _within_start_window(self) -> bool:
        """是否在開放啟動時段(實際時段見 __init__ 的 self.start_window,不在此重複寫死)。"""
        return self.start_window.is_open()

    def can_start(self, user_id: int) -> bool:
        """啟動權限:授權使用者不限時段;其他人僅限開放時段。"""
        return self.start_window.can_start(user_id)

    def start_deny_message(self) -> str:
        return self.start_window.deny_message("Night of the Dead")

    async def _send(self, ctx, content: str):
        try:
            return await ctx.send(content, delete_after=self.delete_delay)
        except Exception as e:
            logger.warning(f"⚠️ 回覆訊息失敗：{e}")
            return None

    async def _send_progress(self, ctx, content: str):
        """送出進度訊息（之後會被編輯成最終結果），失敗則回 None。"""
        try:
            return await ctx.send(content)
        except Exception as e:
            logger.warning(f"⚠️ 送出進度訊息失敗：{e}")
            return None

    async def _update_progress(self, msg, content: str):
        """更新進度訊息內容（沒有訊息或失敗則略過）。"""
        if msg is None:
            return
        try:
            await msg.edit(content=content)
        except Exception:
            pass

    async def _finish_progress(self, msg, ctx, content: str):
        """把進度訊息更新為最終結果並設定自動刪除；沒有進度訊息則改用一般暫時訊息。"""
        if msg is not None:
            try:
                await msg.edit(content=content)
                await msg.delete(delay=self.delete_delay)
                return
            except Exception:
                pass
        await self._send(ctx, content)

    @commands.hybrid_command(name="startnotd")
    async def start_server(self, ctx):
        """啟動 Night of the Dead 伺服器（限開放時段或授權使用者）"""
        if not self.can_start(ctx.author.id):
            logger.warning(f"⛔ 使用者 {ctx.author.id} 在非開放時段嘗試啟動,已拒絕")
            await self._send(ctx, self.start_deny_message())
            return False
        return await self.do_start(ctx)

    async def do_start(self, ctx):
        """實際啟動流程（不含權限檢查），供指令與控制面板共用。"""
        if self.is_process_running():
            logger.warning("⚠️ Night of the Dead 已在執行中")
            await self._send(ctx, "⚠️ Night of the Dead 已在執行中")
            return False

        if not self.base_path or not self.bat_file:
            logger.error("❌ 未設定 NOTD_DIR / NOTD_BAT,請檢查 .env")
            await self._send(ctx, "❌ 尚未設定 Night of the Dead 啟動路徑（NOTD_DIR / NOTD_BAT）")
            return None

        bat_path = os.path.join(self.base_path, self.bat_file)
        if not os.path.isfile(bat_path):
            logger.error(f"❌ 找不到啟動檔：{bat_path}")
            await self._send(ctx, f"❌ 找不到啟動檔：`{bat_path}`")
            return None

        # 每次啟動清空 notd.log,讓本次運行的紀錄純淨
        clear_channel_log("notd")
        logger.info("🆕 Night of the Dead 啟動流程開始,notd.log 已重置")

        try:
            # cwd 必須是伺服器資料夾:該 .bat 內用了 %cd% 與相對路徑
            # (xcopy ServerSettings.ini、LFServer.exe),工作目錄錯了會啟動失敗
            subprocess.Popen(
                bat_path,
                cwd=self.base_path,
                shell=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            self.last_started = datetime.now()
            logger.info("🚀 Night of the Dead 啟動中")
            await self._send(ctx, "🚀 Night of the Dead 啟動中...")
            return True
        except Exception as e:
            logger.error(f"❌ Night of the Dead 啟動失敗：{e.__class__.__name__} - {e}")
            await self._send(ctx, f"❌ Night of the Dead 啟動失敗：{e}")
            return None

    @commands.hybrid_command(name="stopnotd")
    async def stop_server(self, ctx):
        """關閉 Night of the Dead 伺服器"""
        if not self.is_process_running():
            logger.warning("⚠️ Night of the Dead 尚未啟動")
            await self._send(ctx, "⚠️ Night of the Dead 尚未啟動")
            return False

        # 先回饋「關閉中」,讓使用者知道指令已收到(收尾可能需數秒)
        logger.info("🚦 stopnotd 指令收到,開始關閉")
        progress = await self._send_progress(ctx, "⏳ 正在關閉 Night of the Dead...")

        # Night of the Dead 無 RCON/console,只能終止 LFServer 進程
        # 先 terminate 讓它正常收尾,逾時再 kill;其餘 cmd 外殼會隨之 exit。
        procs = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] and self.keyword in proc.info['name']:
                    proc.terminate()
                    procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                logger.debug(f"略過進程：{e}")
                continue

        # 非阻塞輪詢:進程一消失就立刻回報,最多等 ~24 秒,期間不卡住 event loop
        for _ in range(12):
            if not self.is_process_running():
                break
            await asyncio.sleep(2)

        # 仍在執行 → 強制終止
        if self.is_process_running():
            logger.warning("⚠️ Night of the Dead terminate 逾時,改強制終止")
            await self._update_progress(progress, "⚠️ 收尾較久,正在強制關閉...")
            for proc in procs:
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            for _ in range(5):
                if not self.is_process_running():
                    break
                await asyncio.sleep(1)

        if not self.is_process_running():
            logger.info("🛑 Night of the Dead 已完全關閉")
            await self._finish_progress(progress, ctx, "🛑 Night of the Dead 已完全關閉")
            return True

        logger.error("❌ Night of the Dead 關閉失敗,進程仍在執行")
        await self._finish_progress(progress, ctx, "❌ Night of the Dead 關閉失敗,進程仍在執行")
        return None


async def setup(bot):
    await bot.add_cog(NotdServerControl(bot))
