# commands/forwarder.py
import discord
from discord.ext import commands
import json
import os
from config import FORWARDER_CONFIG
from utils.logger import get_logger

logger = get_logger("Forwarder")

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

async def setup(bot):
    await bot.add_cog(Forwarder(bot))
