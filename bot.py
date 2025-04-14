import discord
import asyncio
from discord.ext import commands
from config import BOT_TOKEN

intents = discord.Intents.all()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 要啟用的功能模組 (cogs)     "commands.sevendayserver"
initial_extensions = [
    "commands.forwarder",
    "commands.bdnews",           # <-- 這邊是你剛剛完成的 BD2 模組，名稱為 bdnews.py
    "commands.minecraftserver"
]

@bot.event
async def on_ready():
    print(f"✅ Bot 已上線：{bot.user}")

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
