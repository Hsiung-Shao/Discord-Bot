import discord
import os
from discord.ext import commands

COG_PATH = "commands"  # 請依你實際的目錄結構調整

class CogAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="load")
    async def load_cog(self, ctx, extension: str):
        """📦 載入指定的 Cog 模組"""
        try:
            await self.bot.load_extension(f"{COG_PATH}.{extension}")
            await ctx.send(f"✅ 成功載入：`{extension}`")
        except Exception as e:
            await ctx.send(f"❌ 載入失敗：`{extension}`\n錯誤：```{e}```")

    @commands.command(name="unload")
    async def unload_cog(self, ctx, extension: str):
        """📤 卸載指定的 Cog 模組"""
        try:
            await self.bot.unload_extension(f"{COG_PATH}.{extension}")
            await ctx.send(f"✅ 成功卸載：`{extension}`")
        except Exception as e:
            await ctx.send(f"❌ 卸載失敗：`{extension}`\n錯誤：```{e}```")

    @commands.command(name="reload")
    async def reload_cog(self, ctx, extension: str):
        """🔄 重新載入指定的 Cog 模組"""
        try:
            await self.bot.reload_extension(f"{COG_PATH}.{extension}")
            await ctx.send(f"♻️ 已重新載入：`{extension}`")
        except Exception as e:
            await ctx.send(f"❌ 重新載入失敗：`{extension}`\n錯誤：```{e}```")

    @commands.command(name="listcogs")
    async def list_cogs(self, ctx):
        """📚 顯示目前 `commands/` 資料夾內所有可用的 Cog 檔案（未驗證是否載入）"""
        cogs = [f[:-3] for f in os.listdir(COG_PATH) if f.endswith(".py") and f != "__init__.py"]
        await ctx.send("📁 可用 Cog 模組：\n```\n" + "\n".join(cogs) + "\n```")

async def setup(bot):
    await bot.add_cog(CogAdmin(bot))
