from discord.ext import tasks
from discord.ext.commands import Bot
from config import CONTROL_THREAD_ID
from commands.commandspanel import get_combined_status_embed

def setup_panel_auto_updater(bot: Bot):
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

    @bot.event
    async def on_ready():
        if not update_panel_embed.is_running():
            update_panel_embed.start()
