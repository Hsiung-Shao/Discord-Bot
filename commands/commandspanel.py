import discord
from discord.ext import commands
import asyncio
import psutil
from mcstatus import JavaServer
from datetime import datetime
from config import CONTROL_THREAD_ID

class ServerControlPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

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
                print("✅ 延遲狀態面板更新成功")
            except Exception as e:
                print(f"❌ 更新狀態 Embed 失敗：{e}")
        asyncio.create_task(delayed_status_update())

    @discord.ui.button(label="啟動 Minecraft", style=discord.ButtonStyle.green, custom_id="startmc")
    async def start_mc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction.message)
        cog = self.bot.get_cog("MinecraftServerControl")
        if cog:
            result = await cog.start_server(ctx)
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
        if cog:
            result = await cog.stop_server(ctx)
            if result is True:
                await self.send_temporary_message(interaction.channel, "🛑 Minecraft 關閉成功")
                await self.schedule_status_update(interaction)
            elif result is False:
                await self.send_temporary_message(interaction.channel, "⚠️ Minecraft 尚未啟動")
            else:
                await self.send_temporary_message(interaction.channel, "❌ Minecraft 關閉失敗")

    @discord.ui.button(label="啟動 7 Days", style=discord.ButtonStyle.green, custom_id="start7d")
    async def start_7d(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction.message)
        cog = self.bot.get_cog("SevenDayServerControl")
        if cog:
            result = await cog.start_server(ctx)
            if result is True:
                await self.send_temporary_message(interaction.channel, "✅ 7 Days 啟動成功")
                await self.schedule_status_update(interaction)
            elif result is False:
                await self.send_temporary_message(interaction.channel, "⚠️ 7 Days 已在執行中")
            else:
                await self.send_temporary_message(interaction.channel, "❌ 7 Days 啟動失敗")

    @discord.ui.button(label="關閉 7 Days", style=discord.ButtonStyle.red, custom_id="stop7d")
    async def stop_7d(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction.message)
        cog = self.bot.get_cog("SevenDayServerControl")
        if cog:
            result = await cog.stop_server(ctx)
            if result is True:
                await self.send_temporary_message(interaction.channel, "🛑 7 Days 關閉成功")
                await self.schedule_status_update(interaction)
            elif result is False:
                await self.send_temporary_message(interaction.channel, "⚠️ 7 Days 尚未啟動")
            else:
                await self.send_temporary_message(interaction.channel, "❌ 7 Days 關閉失敗")

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


async def get_combined_status_embed(bot) -> discord.Embed:
    embed = discord.Embed(
        title="📊 伺服器狀態總覽",
        description="目前的伺服器執行狀況如下：",
        color=discord.Color.dark_teal()
    )

    # Minecraft 狀態
    try:
        mc = JavaServer("127.0.0.1", 25565)
        status = mc.status()
        mc_cog = bot.get_cog("MinecraftServerControl")
        last_start = getattr(mc_cog, "last_started", None)
        last_backup = getattr(mc_cog, "last_backup", None)

        mc_info = f"狀態：🟢 在線中\n玩家：{status.players.online} / {status.players.max}\nMOTD：{status.description}"
        if last_start:
            mc_info += f"\n啟動時間：{last_start.strftime('%Y-%m-%d %H:%M:%S')}"
        if last_backup:
            mc_info += f"\n最後備份：{last_backup.strftime('%Y-%m-%d %H:%M:%S')}"

        embed.add_field(name="🟢 Minecraft", value=mc_info, inline=False)
    except Exception:
        embed.add_field(name="🔴 Minecraft", value="伺服器未執行或無法連線。", inline=False)

    # 7 Days to Die 狀態
    try:
        seven_cog = bot.get_cog("SevenDayServerControl")
        last_start = getattr(seven_cog, "last_started", None)
        last_backup = getattr(seven_cog, "last_backup", None)

        running = False
        for proc in psutil.process_iter(['name', 'cmdline']):
            if proc.info['name'] and "7DaysToDieServer" in proc.info['name']:
                running = True
                break

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

    # 額外固定資訊
    embed.add_field(
        name="🌐 伺服器 IP",
        value="`26.82.236.63`  |  `125.228.138.70`",
        inline=False
    )
    embed.add_field(
        name="🔐 Radmin VPN",
        value="`ID: IceRains`\n`密碼: 111222`",
        inline=False
    )

    return embed


# 🔧 註冊 Cog
async def setup(bot):
    await bot.add_cog(CommandPanel(bot))
