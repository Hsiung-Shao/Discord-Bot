# commands/forwarder.py
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from config import FORWARDER_CONFIG
import logging
from utils.logger import get_logger

logger = get_logger("Forwarder", level=logging.WARNING)

class Forwarder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.forward_map = {}
        self.load_forward_config()

    def load_forward_config(self):
        if os.path.exists(FORWARDER_CONFIG):
            with open(FORWARDER_CONFIG, "r", encoding="utf-8") as f:
                self.forward_map = json.load(f)
                logger.info(f"載入 {len(self.forward_map)} 組轉發設定")
                logger.info(f"載入轉發設定：{self.forward_map}")
        else:
            logger.warning(f"{FORWARDER_CONFIG} 不存在，將使用空設定")
            self.forward_map = {}

    def save_forward_config(self):
        with open(FORWARDER_CONFIG, "w", encoding="utf-8") as f:
            json.dump(self.forward_map, f, indent=4)
            logger.info("轉發設定已儲存")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        logger.info(f"[DEBUG] 收到訊息來自使用者 {message.author.id}({message.author})於頻道 {message.channel.id}：{message.content}")
        source_id = str(message.channel.id)
        if source_id not in self.forward_map:
            return

        content = f"💬 {message.author.display_name}: {message.content}" if message.content else None
        files = []
        embeds = []

        # 處理附件
        for attachment in message.attachments:
            try:
                file = await attachment.to_file()
                files.append(file)
            except Exception as e:
                logger.warning(f"附件處理失敗：{e}")

        # 處理嵌入圖片
        for embed in message.embeds:
            if embed.type == "image" and embed.url:
                embeds.append(discord.Embed().set_image(url=embed.url))

        for target_id in self.forward_map[source_id]:
            try:
                target_channel = await self.bot.fetch_channel(int(target_id))
            except discord.NotFound:
                logger.warning(f"找不到目標頻道 {target_id}")
                continue
            except discord.Forbidden:
                logger.warning(f"無權限存取目標頻道 {target_id}")
                continue
            except Exception as e:
                logger.warning(f"取得頻道 {target_id} 失敗: {e}")
                continue

            try:
                if content or files:
                    await target_channel.send(content=content, files=files if files else None)

                for embed in embeds:
                    await target_channel.send(embed=embed)

                logger.info(f"訊息從 {source_id} 轉發到 {target_id}")
            except Exception as e:
                logger.warning(f"轉發失敗至 {target_id}: {e}")

    @commands.command(name="add_forward")
    async def add_forward(self, ctx, source_id: int, target_id: int):
        source_str = str(source_id)
        target_str = str(target_id)

        if source_str not in self.forward_map:
            self.forward_map[source_str] = []

        if target_str not in self.forward_map[source_str]:
            self.forward_map[source_str].append(target_str)
            self.save_forward_config()
            await ctx.send(f"✅ 已新增轉發：{source_id} ➜ {target_id}")
        else:
            await ctx.send("⚠️ 該轉發已存在")

    @commands.command(name="remove_forward")
    async def remove_forward(self, ctx, source_id: int, target_id: int):
        source_str = str(source_id)
        target_str = str(target_id)

        if source_str in self.forward_map and target_str in self.forward_map[source_str]:
            self.forward_map[source_str].remove(target_str)
            if not self.forward_map[source_str]:
                del self.forward_map[source_str]
            self.save_forward_config()
            await ctx.send(f"✅ 已移除轉發：{source_id} ➜ {target_id}")
        else:
            await ctx.send("⚠️ 找不到該轉發規則")

    @commands.hybrid_command(name="forward_status")
    @app_commands.describe(reload="設為 true 會重新讀取 forwarder_map.json 後再回報")
    async def forward_status(self, ctx: commands.Context, reload: bool = False):
        """診斷訊息轉發功能：列出所有規則並檢查來源/目標頻道是否仍可存取"""
        if reload:
            self.load_forward_config()

        if not self.forward_map:
            await ctx.send("⚠️ 目前未載入任何轉發規則。請確認 `data/forwarder_map.json` 是否存在且內容正確。")
            return

        embed = discord.Embed(
            title="🔁 訊息轉發狀態",
            description=f"已載入 **{len(self.forward_map)}** 組來源頻道",
            color=0x3498DB,
        )

        for source_id, target_ids in self.forward_map.items():
            lines = []
            try:
                src_channel = await self.bot.fetch_channel(int(source_id))
                src_label = f"✅ <#{source_id}> (`{src_channel.name}`)"
            except discord.NotFound:
                src_label = f"❌ <#{source_id}> 找不到頻道"
            except discord.Forbidden:
                src_label = f"⚠️ <#{source_id}> 無存取權限"
            except Exception as e:
                src_label = f"⚠️ <#{source_id}> 取得失敗：{e.__class__.__name__}"

            lines.append(f"**來源**：{src_label}")

            for tid in target_ids:
                try:
                    tgt_channel = await self.bot.fetch_channel(int(tid))
                    perms = tgt_channel.permissions_for(tgt_channel.guild.me) if hasattr(tgt_channel, "guild") else None
                    can_send = perms.send_messages if perms else None
                    if can_send is False:
                        lines.append(f"  ➜ ⚠️ <#{tid}> (`{tgt_channel.name}`) 缺 Send Messages 權限")
                    else:
                        lines.append(f"  ➜ ✅ <#{tid}> (`{tgt_channel.name}`)")
                except discord.NotFound:
                    lines.append(f"  ➜ ❌ <#{tid}> 找不到頻道")
                except discord.Forbidden:
                    lines.append(f"  ➜ ⚠️ <#{tid}> 無存取權限")
                except Exception as e:
                    lines.append(f"  ➜ ⚠️ <#{tid}> 取得失敗：{e.__class__.__name__}")

            embed.add_field(name="​", value="\n".join(lines), inline=False)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Forwarder(bot))
