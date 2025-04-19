# tasks/status_updater.py

from discord.ext import tasks
from config import CONTROL_THREAD_ID
from commands.commandspanel import get_combined_status_embed

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
                    print("✅ 控制面板 Embed 已自動更新")
                    return

            print("⚠️ 找不到控制面板訊息，請先使用 !panel 發送控制面板")

        except Exception as e:
            print(f"❌ 自動更新控制面板失敗：{e}")

    _panel_update_task = update_panel_embed
    _panel_update_task.start()
