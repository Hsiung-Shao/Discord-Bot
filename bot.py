import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import discord
import asyncio
from discord.ext import commands
from config import BOT_TOKEN
from config import CONTROL_THREAD_ID
from commands.commandspanel import ServerControlPanelView, get_combined_status_embed
from utils.logger import get_logger
# from tasks.anime_song_scheduler import start_anime_song_updater
logger = get_logger(__name__)
intents = discord.Intents.all()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 要啟用的功能模組 (cogs)     "commands.sevendayserver"
initial_extensions = [
    "commands.forwarder",
    "commands.bdnews",           # <-- 這邊是你剛剛完成的 BD2 模組，名稱為 bdnews.py
    "commands.minecraftserver",
    "commands.sevendayserver",
    "commands.commandspanel",
    "commands.admin"
]

@bot.event
async def on_ready():
    print(f"✅ Bot 已上線：{bot.user}")
    asyncio.create_task(initialize_panel(bot))

async def initialize_panel(bot):
    try:
        channel = await bot.fetch_channel(CONTROL_THREAD_ID)
        async for msg in channel.history(limit=None, oldest_first=True):
            if msg.author == bot.user:
                try:
                    await msg.delete()
                    await asyncio.sleep(0.3)  # 避免 rate limit
                except Exception as e:
                    logger.warning(f"⚠️ 刪除訊息失敗：{e}")
        logger.info("🧹 已刪除所有機器人歷史訊息")
    except Exception as e:
        logger.warning(f"⚠️ 無法清除舊訊息：{e}")

    try:
        embed = await get_combined_status_embed(bot)
        view = ServerControlPanelView(bot)
        msg = await channel.send(embed=embed, view=view)
        bot.add_view(view)
        logger.info(f"📤 已發送新的控制面板訊息 ID: {msg.id}")
    except Exception as e:
        logger.error(f"❌ 發送新控制面板失敗：{e}")

    try:
        from tasks.panel_updater import setup_panel_auto_updater
        setup_panel_auto_updater(bot)
        logger.info("🛠️ 面板狀態更新排程已啟動")
    except Exception as e:
        logger.error(f"❌ 啟動面板自動更新任務失敗：{e}")

@bot.event
async def on_command_error(ctx, error):
    await ctx.send(f"❌ 指令錯誤：{type(error).__name__} - {error}")

async def main():
    async with bot:
        for ext in initial_extensions:
            try:
                await bot.load_extension(ext)
                logger.info(f"✅ 成功載入模組：{ext}")
            except Exception as e:
                logger.info(f"❌ 載入模組失敗：{ext}，錯誤：{e}")
        await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
