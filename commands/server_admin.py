from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from commands.mc_server_config import (
    MinecraftServerProfile,
    add_server,
    build_profile_from_template,
    load_servers,
    load_templates,
    remove_server,
    set_server_disabled,
)
from utils.logger import get_logger

logger = get_logger(__name__, channel="minecraft")


class ServerAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _refresh_minecraft_cog(self) -> None:
        cog = self.bot.get_cog("MinecraftServerControl")
        if cog and hasattr(cog, "reload_servers"):
            cog.reload_servers()

    @commands.hybrid_command(name="server_list")
    async def server_list(self, ctx: commands.Context):
        """列出所有 Minecraft 伺服器設定（包含已停用的）"""
        servers = load_servers(include_disabled=True)
        if not servers:
            await ctx.send("⚠️ 尚未設定任何伺服器")
            return

        embed = discord.Embed(title="🖥️ Minecraft 伺服器清單", color=0x3498DB)
        for s in servers:
            state = "🔴 停用" if s.disabled else "🟢 啟用"
            embed.add_field(
                name=f"`{s.id}` — {s.name}",
                value=(
                    f"狀態：{state}\n"
                    f"路徑：`{s.base_path}`\n"
                    f"啟動：`{s.start_bat}`　連線：{s.host}:{s.game_port}(RCON {s.rcon_port})\n"
                    f"自動備份：{'✅' if s.auto_backup else '❌'}"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="server_templates")
    async def server_templates(self, ctx: commands.Context):
        """列出可用的伺服器模板（給 /server_add 的 template 參數用）"""
        tpls = load_templates()
        if not tpls:
            await ctx.send("⚠️ 尚未定義任何模板，請編輯 `data/minecraft_server_templates.json`")
            return
        embed = discord.Embed(title="📋 可用模板", color=0x9B59B6)
        for tid, t in tpls.items():
            embed.add_field(name=f"`{tid}`", value=t.get("description", "(無描述)"), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="server_add")
    @app_commands.describe(
        server_id="伺服器 ID（英數，簡短易記，例如 atm10）",
        name="顯示名稱（可中文）",
        base_path="伺服器資料夾絕對路徑",
        template="模板 ID（可用 /server_templates 查詢；省略則需自行帶齊欄位）",
        rcon_password="RCON 密碼（覆蓋模板預設）",
        game_port="遊戲埠（覆蓋模板預設）",
        rcon_port="RCON 埠（覆蓋模板預設）",
        start_bat="啟動腳本檔名（覆蓋模板預設，例 startserver.bat）",
        startup_timeout="啟動 RCON 連線最長等待秒數",
    )
    async def server_add(
        self,
        ctx: commands.Context,
        server_id: str,
        name: str,
        base_path: str,
        template: Optional[str] = None,
        rcon_password: Optional[str] = None,
        game_port: Optional[int] = None,
        rcon_port: Optional[int] = None,
        start_bat: Optional[str] = None,
        startup_timeout: Optional[int] = None,
    ):
        """新增 Minecraft 伺服器設定。建議搭配 template 使用，僅需提供 id/name/path"""
        try:
            overrides: dict = {}
            if rcon_password:
                overrides["rcon_password"] = rcon_password
            if game_port:
                overrides["game_port"] = game_port
            if rcon_port:
                overrides["rcon_port"] = rcon_port
            if start_bat:
                overrides["start_bat"] = start_bat
            if startup_timeout:
                overrides["startup_timeout"] = startup_timeout

            if template:
                profile = build_profile_from_template(template, server_id, name, base_path, overrides)
            else:
                profile = MinecraftServerProfile(
                    id=server_id,
                    name=name,
                    base_path=base_path,
                    start_bat=start_bat or "run.bat",
                    jar_keyword="forge",
                    host="127.0.0.1",
                    game_port=game_port or 25565,
                    rcon_port=rcon_port or 25575,
                    rcon_password=rcon_password or "changeme",
                    startup_timeout=startup_timeout or 300,
                )

            add_server(profile)
            await self._refresh_minecraft_cog()
            await ctx.send(
                f"✅ 已新增伺服器：`{profile.id}` ({profile.name})\n"
                f"路徑：`{profile.base_path}`\n"
                f"連線：{profile.host}:{profile.game_port}　RCON {profile.rcon_port}\n"
                "⚠️ 提醒：請確認 RCON 密碼、啟動腳本與目錄存在後再執行 `/startmc`。"
            )
        except (ValueError, TypeError) as e:
            await ctx.send(f"❌ 新增失敗：{e}")

    @commands.hybrid_command(name="server_remove")
    @app_commands.describe(server_id="要移除的伺服器 ID（資料夾與世界檔不會被刪）")
    async def server_remove(self, ctx: commands.Context, server_id: str):
        """從設定中永久移除指定伺服器（不會刪除實體檔案）"""
        if remove_server(server_id):
            await self._refresh_minecraft_cog()
            await ctx.send(f"🗑️ 已移除伺服器設定：`{server_id}`")
        else:
            await ctx.send(f"⚠️ 找不到伺服器：`{server_id}`")

    @commands.hybrid_command(name="server_disable")
    @app_commands.describe(server_id="要停用的伺服器 ID")
    async def server_disable(self, ctx: commands.Context, server_id: str):
        """停用伺服器（從面板/啟動清單隱藏，但設定保留）"""
        if set_server_disabled(server_id, True):
            await self._refresh_minecraft_cog()
            await ctx.send(f"🔴 已停用：`{server_id}`")
        else:
            await ctx.send(f"⚠️ 找不到伺服器：`{server_id}`")

    @commands.hybrid_command(name="server_enable")
    @app_commands.describe(server_id="要重新啟用的伺服器 ID")
    async def server_enable(self, ctx: commands.Context, server_id: str):
        """重新啟用先前被停用的伺服器"""
        if set_server_disabled(server_id, False):
            await self._refresh_minecraft_cog()
            await ctx.send(f"🟢 已啟用：`{server_id}`")
        else:
            await ctx.send(f"⚠️ 找不到伺服器：`{server_id}`")

    @commands.hybrid_command(name="server_reload")
    async def server_reload(self, ctx: commands.Context):
        """重新讀取 minecraft_servers.json（手動編輯設定檔後執行）"""
        await self._refresh_minecraft_cog()
        servers = load_servers()
        await ctx.send(f"🔄 已重新載入。目前啟用 {len(servers)} 個伺服器")


async def setup(bot):
    await bot.add_cog(ServerAdmin(bot))
