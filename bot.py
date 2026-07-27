import sys
import io
import os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import discord
import asyncio
from discord.ext import commands
from config import BOT_TOKEN
from config import CONTROL_THREAD_ID
from config import GUILD_IDS
from commands.commandspanel import ServerControlPanelView, get_combined_status_embed
from backups.manager import BackupManager
from backups.minecraft_backup import MinecraftBackupHandler
from backups.seven_days_backup import SevenDaysBackupHandler
from commands.mc_server_config import load_servers
from config import SEVENDAY_SAVE_PATH, BACKUP_ROOT
from utils.logger import get_logger
from tasks.auto_backup_task import AutoBackupTask
from tasks.log_compressor import LogCompressor

logger = get_logger(__name__)
intents = discord.Intents.all()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 要啟用的功能模組 (cogs)
initial_extensions = [
    "commands.forwarder",
    "commands.bdnews",
    "commands.minecraftserver",
    "commands.sevendayserver",
    "commands.commandspanel",
    "commands.admin",
    "commands.lol",
    "commands.x_tracker",
    "commands.ff14news",
    "commands.wuwanews",
    "commands.server_admin",
    "commands.daily_news",
    "commands.translator",
    "commands.release_notify",
]

@bot.event
async def on_ready():
    logger.info(f"✅ Bot 已上線：{bot.user}")

    # 同步 slash commands 到指定伺服器（即時生效）
    for gid in GUILD_IDS:
        guild = discord.Object(id=gid)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"✅ 已同步 {len(synced)} 個 slash commands 到 Guild {gid}")

    asyncio.create_task(initialize_panel(bot))
    await asyncio.sleep(5)

    backup_manager = BackupManager()

    # 為每個啟用 auto_backup 的 Minecraft 伺服器註冊備份 handler
    for profile in load_servers():
        if not profile.auto_backup:
            continue
        backup_manager.register_handler(
            MinecraftBackupHandler(
                server_id=profile.id,
                display_name=profile.name,
                world_path=profile.world_path,
                backup_root=BACKUP_ROOT
            )
        )
        logger.info(f"📦 已註冊 Minecraft 備份 handler：{profile.name}")

    backup_manager.register_handler(
        SevenDaysBackupHandler(
            save_path=SEVENDAY_SAVE_PATH,
            backup_root=BACKUP_ROOT
        )
    )
    bot.backup_manager = backup_manager
    # 初始化備份任務
    bot.backup_task = AutoBackupTask(bot)
    LogCompressor(bot)
    logger.info("📦 自動備份任務已註冊")

async def initialize_panel(bot):
    try:
        channel = await bot.fetch_channel(CONTROL_THREAD_ID)
        async for msg in channel.history(limit=None, oldest_first=True):
            if msg.author == bot.user:
                try:
                    await msg.delete()
                    await asyncio.sleep(0.3)
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

        # ── 載入被 gitignore 的私密 extension 清單(若存在) ──
        # 清單檔每行一個 extension 模組路徑(如 commands.feedback_tracker)。
        # 此段通用程式碼不含任何私密名稱可進版控;清單檔與其列出的 cog 皆已 gitignore。
        private_ext_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "commands", "_private_ext.txt"
        )
        if os.path.exists(private_ext_file):
            try:
                with open(private_ext_file, "r", encoding="utf-8") as f:
                    private_exts = [
                        ln.strip() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
            except Exception as e:
                private_exts = []
                logger.info(f"❌ 讀取私密 extension 清單失敗：{e}")
            for ext in private_exts:
                try:
                    await bot.load_extension(ext)
                    logger.info(f"✅ 成功載入私密模組：{ext}")
                except Exception as e:
                    logger.info(f"❌ 載入私密模組失敗：{ext}，錯誤：{e}")

        await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
