# tasks/panel_updater.py
# 控制面板狀態 embed 自動更新:每 5 分鐘重抓一次伺服器狀態並編輯面板訊息。
# 正常更新不留紀錄,只有找不到面板或編輯失敗才寫進 bot.log。

import logging

from discord.ext import tasks
from config import CONTROL_THREAD_ID
from commands.commandspanel import get_combined_status_embed
from utils.logger import get_logger

logger = get_logger(__name__, level=logging.WARNING)

# 任務變數定義在全域，避免重複定義
_panel_update_task = None

def setup_panel_auto_updater(bot):
    global _panel_update_task

    # 若任務已經存在，就不重複建立
    if _panel_update_task is not None:
        return

    @tasks.loop(minutes=5)
    async def update_panel_embed():
        try:
            channel = await bot.fetch_channel(CONTROL_THREAD_ID)
            messages = [msg async for msg in channel.history(limit=10)]

            for msg in messages:
                if msg.author == bot.user and msg.embeds and msg.components:
                    embed = await get_combined_status_embed(bot)
                    await msg.edit(embed=embed)
                    return

            logger.warning("找不到控制面板訊息，請先使用 !panel 發送控制面板")

        except Exception as e:
            logger.error(f"自動更新控制面板失敗：{e}")

    @update_panel_embed.before_loop
    async def _wait_ready():
        await bot.wait_until_ready()

    _panel_update_task = update_panel_embed
    _panel_update_task.start()
