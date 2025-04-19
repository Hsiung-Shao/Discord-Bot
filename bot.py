import discord
import asyncio
from discord.ext import commands
from config import BOT_TOKEN
from commands.commandspanel import ServerControlPanelView, get_combined_status_embed
from config import CONTROL_THREAD_ID

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
    "commands.anime_songs",
    "commands.admin"
]

@bot.event
async def on_ready():
    print(f"✅ Bot 已上線：{bot.user}")

    # 🔁 1. 清除舊面板
    try:
        channel = await bot.fetch_channel(CONTROL_THREAD_ID)
        async for msg in channel.history(limit=10):
            if msg.author == bot.user and msg.embeds and msg.components:
                await msg.delete()
                print("🧹 已刪除過期的控制面板訊息")
    except Exception as e:
        print(f"⚠️ 無法清除舊面板訊息：{e}")

    # 📤 2. 發送新面板並註冊 View
    try:
        embed = await get_combined_status_embed(bot)
        view = ServerControlPanelView(bot)
        await channel.send(embed=embed, view=view)
        bot.add_view(view)
        print("📤 已重新發送新的控制面板")
    except Exception as e:
        print(f"❌ 發送新控制面板失敗：{e}")

    # 🔁 3. 啟動自動更新面板狀態任務（在 View 註冊後）
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
