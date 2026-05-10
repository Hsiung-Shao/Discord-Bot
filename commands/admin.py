import discord
import os
from discord.ext import commands

from config import GUILD_IDS

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

    @commands.command(name="purge_commands")
    @commands.is_owner()
    async def purge_commands(self, ctx, scope: str = "guild"):
        """🧹 清除 slash 指令並重新同步。

        用法：
          !purge_commands         → 清空 GUILD_IDS 中所有 guild 的指令並重同步
          !purge_commands guild   → 同上
          !purge_commands global  → 清空全域指令(全域 sync 約 1 小時生效)
          !purge_commands all     → 全域 + 所有 guild 都清
        """
        scope = scope.lower()
        results = []

        if scope in ("guild", "all"):
            for gid in GUILD_IDS:
                guild = discord.Object(id=gid)
                self.bot.tree.clear_commands(guild=guild)
                await self.bot.tree.sync(guild=guild)
                results.append(f"✅ 已清空 Guild {gid} 的所有 slash 指令")

        if scope in ("global", "all"):
            self.bot.tree.clear_commands(guild=None)
            await self.bot.tree.sync()
            results.append("✅ 已清空全域 slash 指令(約 1 小時內生效)")

        if not results:
            await ctx.send(f"❌ 未知的 scope：`{scope}`(可用:guild / global / all)")
            return

        await ctx.send("\n".join(results) + "\n\n💡 接著執行 `!resync_commands` 把目前載入的指令重新同步。")

    @commands.command(name="resync_commands")
    @commands.is_owner()
    async def resync_commands(self, ctx):
        """🔄 把目前載入的 cog 中的 slash 指令重新同步到 GUILD_IDS"""
        results = []
        for gid in GUILD_IDS:
            guild = discord.Object(id=gid)
            self.bot.tree.copy_global_to(guild=guild)
            synced = await self.bot.tree.sync(guild=guild)
            results.append(f"✅ Guild {gid}：同步 {len(synced)} 個指令")
        await ctx.send("\n".join(results))

    @commands.command(name="sync_here")
    @commands.is_owner()
    async def sync_here(self, ctx):
        """🔁 把當前 cog 的所有 slash 指令同步到本伺服器(當下這個 guild)"""
        if ctx.guild is None:
            await ctx.send("❌ 此指令必須在伺服器中執行(不能在 DM)")
            return
        guild = discord.Object(id=ctx.guild.id)
        self.bot.tree.copy_global_to(guild=guild)
        synced = await self.bot.tree.sync(guild=guild)
        await ctx.send(f"✅ 已同步 **{len(synced)}** 個 slash 指令到本伺服器 (Guild {ctx.guild.id})")

    @commands.command(name="reload_all")
    @commands.is_owner()
    async def reload_all(self, ctx):
        """♻️ 重新載入所有 cog,並把所有 slash 指令重新註冊到 bot.tree"""
        # 先取得目前所有已載入的 extensions
        loaded = list(self.bot.extensions.keys())
        ok, fail = [], []
        for ext in loaded:
            try:
                await self.bot.reload_extension(ext)
                ok.append(ext)
            except Exception as e:
                fail.append(f"{ext}: {e}")

        msg = f"✅ 重新載入 {len(ok)} 個 cog"
        if fail:
            msg += f"\n❌ 失敗 {len(fail)} 個:\n```\n" + "\n".join(fail) + "\n```"

        # 列出 tree 上現在有幾個 slash commands
        tree_cmds = self.bot.tree.get_commands()
        msg += f"\n📋 bot.tree 目前有 **{len(tree_cmds)}** 個 slash 指令"
        msg += "\n💡 接著執行 `!sync_here` 把它們同步到本伺服器"
        await ctx.send(msg)

    @commands.command(name="purge_here")
    @commands.is_owner()
    async def purge_here(self, ctx):
        """🧹 清空本伺服器(當下這個 guild)的所有 slash 指令"""
        if ctx.guild is None:
            await ctx.send("❌ 此指令必須在伺服器中執行")
            return
        guild = discord.Object(id=ctx.guild.id)
        self.bot.tree.clear_commands(guild=guild)
        await self.bot.tree.sync(guild=guild)
        await ctx.send(f"✅ 已清空 Guild {ctx.guild.id} 的所有 slash 指令\n💡 接著執行 `!sync_here` 重新同步")

    @commands.command(name="purge_global")
    @commands.is_owner()
    async def purge_global(self, ctx):
        """🌍 清空全域 slash 指令(會清掉以前 global sync 留下的舊指令,生效約 1 小時)"""
        self.bot.tree.clear_commands(guild=None)
        await self.bot.tree.sync()
        await ctx.send(
            "✅ 已清空全域 slash 指令\n"
            "⚠️ Discord 全域 sync 約需 1 小時生效\n"
            "💡 接著用 `!sync_here` 把指令同步到本伺服器(會立刻生效)"
        )

    @commands.command(name="list_slash")
    @commands.is_owner()
    async def list_slash(self, ctx):
        """📋 列出當前已註冊到 tree 的所有 slash 指令(尚未 sync 不在內)"""
        cmds = self.bot.tree.get_commands()
        if not cmds:
            await ctx.send("📭 目前沒有 slash 指令")
            return
        names = sorted(c.name for c in cmds)
        chunks = []
        chunk = ""
        for n in names:
            line = f"• `/{n}`\n"
            if len(chunk) + len(line) > 1900:
                chunks.append(chunk)
                chunk = ""
            chunk += line
        if chunk:
            chunks.append(chunk)
        await ctx.send(f"📋 共 **{len(names)}** 個 slash 指令:\n{chunks[0]}")
        for c in chunks[1:]:
            await ctx.send(c)


async def setup(bot):
    await bot.add_cog(CogAdmin(bot))
