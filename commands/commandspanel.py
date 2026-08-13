import discord
from discord.ext import commands
import asyncio
import psutil
from mcstatus import JavaServer
from datetime import datetime
from config import CONTROL_THREAD_ID
from pytz import timezone
from commands.mc_server_config import load_servers
from commands.panel_config import load_panel_config
from utils.mc_log import detect_clean_shutdown


class MinecraftServerSelect(discord.ui.Select):
    def __init__(self, servers, default_id=None):
        options = []
        for s in servers:
            options.append(
                discord.SelectOption(
                    label=s.name,
                    value=s.id,
                    description=f"{s.host}:{s.game_port}",
                    default=(s.id == default_id)
                )
            )
        super().__init__(
            placeholder="選擇要操作的 Minecraft 伺服器...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="mc_server_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view: "ServerControlPanelView" = self.view
        view.selected_mc_server_id = self.values[0]
        for opt in self.options:
            opt.default = (opt.value == self.values[0])
        await interaction.response.edit_message(view=view)


class ServerControlPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        servers = load_servers()
        self.selected_mc_server_id = servers[0].id if servers else None
        if len(servers) > 1:
            self.add_item(MinecraftServerSelect(servers, default_id=self.selected_mc_server_id))

    async def send_temporary_message(self, thread, content, delay=10):
        msg = await thread.send(content)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except discord.NotFound:
            pass

    async def schedule_status_update(self, interaction: discord.Interaction, delay_seconds: int = 60):
        async def delayed_status_update():
            await asyncio.sleep(delay_seconds)
            embed = await get_combined_status_embed(self.bot)
            try:
                await interaction.message.edit(embed=embed)
            except Exception as e:
                print(f"❌ 更新狀態 Embed 失敗：{e}")
        asyncio.create_task(delayed_status_update())

    @discord.ui.button(label="啟動 Minecraft", style=discord.ButtonStyle.green, custom_id="startmc")
    async def start_mc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction.message)
        cog = self.bot.get_cog("MinecraftServerControl")
        if not cog:
            await self.send_temporary_message(interaction.channel, "❌ Minecraft Cog 未載入")
            return

        profile = cog.get_profile(self.selected_mc_server_id)
        if not profile:
            await self.send_temporary_message(
                interaction.channel, f"❌ 找不到伺服器 `{self.selected_mc_server_id}`"
            )
            return

        # 權限以「真正按按鈕的人」判斷（面板的 ctx.author 會是 bot 自己）
        if not cog.can_start(profile, interaction.user.id):
            await self.send_temporary_message(
                interaction.channel, cog.start_deny_message(profile), delay=30
            )
            return

        result = await cog.do_start(ctx, profile)
        if result is True:
            await self.send_temporary_message(interaction.channel, "✅ Minecraft 啟動成功")
            await self.schedule_status_update(interaction)
        elif result is False:
            await self.send_temporary_message(interaction.channel, "⚠️ Minecraft 已在執行中")
        else:
            await self.send_temporary_message(interaction.channel, "❌ Minecraft 啟動失敗")

    @discord.ui.button(label="關閉 Minecraft", style=discord.ButtonStyle.red, custom_id="stopmc")
    async def stop_mc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction.message)
        cog = self.bot.get_cog("MinecraftServerControl")
        if not cog:
            await self.send_temporary_message(interaction.channel, "❌ Minecraft Cog 未載入")
            return
        result = await cog.stop_server(ctx, self.selected_mc_server_id)
        if result is True:
            await self.send_temporary_message(interaction.channel, "🛑 Minecraft 關閉成功")
            await self.schedule_status_update(interaction)
        elif result is False:
            # 按鈕操作的是「下方選單選到的那台」，選錯時光說「尚未啟動」會讓人一頭霧水
            msg = "⚠️ Minecraft 尚未啟動"
            others = [
                p.name for p in cog.list_servers()
                if p.id != self.selected_mc_server_id and cog.is_process_running(p)
            ]
            if others:
                msg += f"\n　└ 目前在執行的是：{'、'.join(others)}，請先在下方選單切換到該伺服器"
            await self.send_temporary_message(interaction.channel, msg, delay=30)
        else:
            await self.send_temporary_message(interaction.channel, "❌ Minecraft 關閉失敗")

    # @discord.ui.button(label="啟動 7 Days", style=discord.ButtonStyle.green, custom_id="start7d")
    # async def start_7d(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await interaction.response.defer()
    #     ctx = await self.bot.get_context(interaction.message)
    #     cog = self.bot.get_cog("SevenDayServerControl")
    #     if cog:
    #         result = await cog.start_server(ctx)
    #         if result is True:
    #             await self.send_temporary_message(interaction.channel, "✅ 7 Days 啟動成功")
    #             await self.schedule_status_update(interaction)
    #         elif result is False:
    #             await self.send_temporary_message(interaction.channel, "⚠️ 7 Days 已在執行中")
    #         else:
    #             await self.send_temporary_message(interaction.channel, "❌ 7 Days 啟動失敗")

    # @discord.ui.button(label="關閉 7 Days", style=discord.ButtonStyle.red, custom_id="stop7d")
    # async def stop_7d(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await interaction.response.defer()
    #     ctx = await self.bot.get_context(interaction.message)
    #     cog = self.bot.get_cog("SevenDayServerControl")
    #     if cog:
    #         result = await cog.stop_server(ctx)
    #         if result is True:
    #             await self.send_temporary_message(interaction.channel, "🛑 7 Days 關閉成功")
    #             await self.schedule_status_update(interaction)
    #         elif result is False:
    #             await self.send_temporary_message(interaction.channel, "⚠️ 7 Days 尚未啟動")
    #         else:
    #             await self.send_temporary_message(interaction.channel, "❌ 7 Days 關閉失敗")

    # @discord.ui.button(label="啟動 Night of the Dead", style=discord.ButtonStyle.green, custom_id="startnotd")
    # async def start_notd(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await interaction.response.defer()
    #     cog = self.bot.get_cog("NotdServerControl")
    #     if not cog:
    #         await self.send_temporary_message(interaction.channel, "❌ Night of the Dead Cog 未載入")
    #         return
    #     # 權限以「真正按按鈕的人」判斷(面板 ctx.author 會是 bot 自己)
    #     if not cog.can_start(interaction.user.id):
    #         await self.send_temporary_message(interaction.channel, cog.start_deny_message())
    #         return
    #     ctx = await self.bot.get_context(interaction.message)
    #     result = await cog.do_start(ctx)
    #     if result is True:
    #         await self.send_temporary_message(interaction.channel, "✅ Night of the Dead 啟動成功")
    #         await self.schedule_status_update(interaction)
    #     elif result is False:
    #         await self.send_temporary_message(interaction.channel, "⚠️ Night of the Dead 已在執行中")
    #     else:
    #         await self.send_temporary_message(interaction.channel, "❌ Night of the Dead 啟動失敗")

    # @discord.ui.button(label="關閉 Night of the Dead", style=discord.ButtonStyle.red, custom_id="stopnotd")
    # async def stop_notd(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await interaction.response.defer()
    #     ctx = await self.bot.get_context(interaction.message)
    #     cog = self.bot.get_cog("NotdServerControl")
    #     if cog:
    #         result = await cog.stop_server(ctx)
    #         if result is True:
    #             await self.send_temporary_message(interaction.channel, "🛑 Night of the Dead 關閉成功")
    #             await self.schedule_status_update(interaction)
    #         elif result is False:
    #             await self.send_temporary_message(interaction.channel, "⚠️ Night of the Dead 尚未啟動")
    #         else:
    #             await self.send_temporary_message(interaction.channel, "❌ Night of the Dead 關閉失敗")

    @discord.ui.button(label="查詢狀態", style=discord.ButtonStyle.blurple, custom_id="status")
    async def check_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = await get_combined_status_embed(self.bot)
        await interaction.message.edit(embed=embed)


class CommandPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="panel")
    async def send_control_panel(self, ctx):
        thread = await self.bot.fetch_channel(CONTROL_THREAD_ID)
        embed = await get_combined_status_embed(self.bot)
        await thread.send(embed=embed, view=ServerControlPanelView(self.bot))


async def safe_process_iter():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: list(psutil.process_iter(['name', 'cmdline'])))


async def _add_minecraft_fields(embed: discord.Embed, bot, show_start_window: bool) -> None:
    mc_cog = bot.get_cog("MinecraftServerControl")
    servers = mc_cog.list_servers() if mc_cog else load_servers()

    if not servers:
        embed.add_field(name="⚠️ Minecraft", value="尚未設定任何伺服器", inline=False)
        return

    for profile in servers:
        # 先檢查這個 profile 對應的進程是否真的在跑（避免共用 port 導致誤判）
        is_running = mc_cog.is_process_running(profile) if mc_cog else False

        # 有設開放時段限制時才顯示，沒設定的伺服器不會多出一行雜訊
        window_line = ""
        if show_start_window and profile.window.enabled:
            window_line = f"\n開放啟動：{profile.window.describe()}（{profile.window.tz_display}）"

        if not is_running:
            embed.add_field(
                name=f"🔴 {profile.name}",
                value=f"伺服器未執行。\n位址：`{profile.host}:{profile.game_port}`{window_line}",
                inline=False
            )
            continue

        last_start = mc_cog.last_started.get(profile.id) if mc_cog else None
        last_backup = mc_cog.last_backup.get(profile.id) if mc_cog else None

        try:
            mc = JavaServer(profile.host, profile.game_port)
            status = await mc.async_status()
            badge = "🟢"
            info = (
                f"狀態：🟢 在線中\n"
                f"玩家：{status.players.online} / {status.players.max}\n"
                f"位址：`{profile.host}:{profile.game_port}`"
            )
        except Exception:
            # 進程在跑但查詢不到狀態。這有兩種完全不同的情況，不能都說「載入中」：
            # 若 log 已出現完整關閉序列，代表這是「存完檔但 JVM 卡住沒退出」的殘留進程。
            saved, _ = await asyncio.to_thread(detect_clean_shutdown, profile.console_log_path)
            if saved:
                badge = "🟠"
                info = (
                    f"狀態：🟠 殘留進程（已關閉但未退出）\n"
                    f"世界已完成存檔，**在下方選單選到本伺服器**後按「關閉 Minecraft」即可清除\n"
                    f"位址：`{profile.host}:{profile.game_port}`"
                )
            else:
                badge = "🟡"
                info = (
                    f"狀態：🟡 載入中或 RCON 未就緒\n"
                    f"位址：`{profile.host}:{profile.game_port}`"
                )

        info += window_line
        if last_start:
            info += f"\n啟動時間：{last_start.strftime('%Y-%m-%d %H:%M:%S')}"
        if last_backup:
            info += f"\n最後備份：{last_backup.strftime('%Y-%m-%d %H:%M:%S')}"
        embed.add_field(name=f"{badge} {profile.name}", value=info, inline=False)


async def _add_sevendays_fields(embed: discord.Embed, bot) -> None:
    try:
        seven_cog = bot.get_cog("SevenDayServerControl")
        last_start = getattr(seven_cog, "last_started", None)
        last_backup = getattr(seven_cog, "last_backup", None)

        running = False
        processes = await safe_process_iter()
        for proc in processes:
            try:
                if proc.info['name'] and "7DaysToDieServer" in proc.info['name']:
                    running = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if running:
            info = "狀態：🟢 在線中"
            if last_start:
                info += f"\n啟動時間：{last_start.strftime('%Y-%m-%d %H:%M:%S')}"
            if last_backup:
                info += f"\n最後備份：{last_backup.strftime('%Y-%m-%d %H:%M:%S')}"
            embed.add_field(name="🟢 7 Days to Die", value=info, inline=False)
        else:
            embed.add_field(name="🔴 7 Days to Die", value="伺服器未執行。", inline=False)
    except Exception as e:
        embed.add_field(name="⚠️ 7 Days 狀態錯誤", value=str(e), inline=False)


async def _add_notd_fields(embed: discord.Embed, bot) -> None:
    try:
        notd_cog = bot.get_cog("NotdServerControl")
        if notd_cog and notd_cog.is_process_running():
            info = "狀態：🟢 在線中"
            last_start = getattr(notd_cog, "last_started", None)
            if last_start:
                info += f"\n啟動時間：{last_start.strftime('%Y-%m-%d %H:%M:%S')}"
            embed.add_field(name="🟢 Night of the Dead", value=info, inline=False)
        else:
            embed.add_field(name="🔴 Night of the Dead", value="伺服器未執行。", inline=False)
    except Exception as e:
        embed.add_field(name="⚠️ Night of the Dead 狀態錯誤", value=str(e), inline=False)


async def get_combined_status_embed(bot) -> discord.Embed:
    """產生面板 embed。顯示內容由 data/panel_config.json 控制（改檔即生效，不必重啟）。"""
    cfg = load_panel_config()

    embed = discord.Embed(
        title=cfg["title"],
        description=cfg["description"],
        color=discord.Color.dark_teal()
    )

    if cfg.get("show_minecraft", True):
        await _add_minecraft_fields(embed, bot, cfg.get("show_start_window", True))
    if cfg.get("show_sevendays", False):
        await _add_sevendays_fields(embed, bot)
    if cfg.get("show_notd", False):
        await _add_notd_fields(embed, bot)

    # 額外資訊（IP、VPN 帳密等）皆由設定檔提供
    for field in cfg.get("extra_fields", []):
        embed.add_field(name=field["name"], value=field["value"], inline=field.get("inline", False))

    tz = timezone("Asia/Taipei")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    embed.set_footer(text=f"🕒 最後更新時間：{now}")

    return embed


async def setup(bot):
    await bot.add_cog(CommandPanel(bot))
