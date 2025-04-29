import discord
import asyncio
from discord.ext import commands
from config import BOT_TOKEN
from config import CONTROL_THREAD_ID
from commands.commandspanel import ServerControlPanelView, get_combined_status_embed
# from tasks.anime_song_scheduler import start_anime_song_updater

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
        async for msg in channel.history(limit=10):
            if msg.author == bot.user and msg.embeds and msg.components:
                await msg.delete()
                print("🧹 已刪除過期的控制面板訊息")
    except Exception as e:
        print(f"⚠️ 無法清除舊面板訊息：{e}")

    try:
        embed = await get_combined_status_embed(bot)
        view = ServerControlPanelView(bot)
        await channel.send(embed=embed, view=view)
        bot.add_view(view)
        print("📤 已重新發送新的控制面板")
    except Exception as e:
        print(f"❌ 發送新控制面板失敗：{e}")

    try:
        from tasks.panel_updater import setup_panel_auto_updater
        setup_panel_auto_updater(bot)
        print("🛠️ 面板狀態更新排程已啟動")
    except Exception as e:
        print(f"❌ 啟動面板自動更新任務失敗：{e}")

@bot.event
async def on_command_error(ctx, error):
    await ctx.send(f"❌ 指令錯誤：{type(error).__name__} - {error}")

async def main():
    async with bot:
        for ext in initial_extensions:
            try:
                await bot.load_extension(ext)
                print(f"✅ 成功載入模組：{ext}")
            except Exception as e:
                print(f"❌ 載入模組失敗：{ext}，錯誤：{e}")
        await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
