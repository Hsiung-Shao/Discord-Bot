# commands/musicgame.py

import discord
from discord.ext import commands
from discord import ui, Interaction, Embed, SelectOption
import json
import random
from pathlib import Path
from commands.musicgame_session import MusicGameSession, active_sessions

SONG_DATA_PATH = Path("data/anime_songs.json")

class MusicGameLauncher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="musicgame")
    async def launch_music_game(self, ctx):
        await ctx.send("🎶 請選擇遊戲模式：", view=GameModeSelectorView(self.bot, ctx))

    @commands.hybrid_command(name="transferhost")
    async def transfer_host(self, ctx, member: discord.Member):
        session = active_sessions.get(ctx.channel.id)
        if not session:
            await ctx.send("❌ 此頻道沒有進行中的比賽。")
            return
        if ctx.author.id != session.host_user_id:
            await ctx.send("⚠️ 只有主持人可以轉移主持權。")
            return

        await session.transfer_host(member)


# UI：模式選擇
class GameModeSelectorView(ui.View):
    def __init__(self, bot, ctx):
        super().__init__(timeout=60)
        self.bot = bot
        self.ctx = ctx

        self.mode_select = ui.Select(
            placeholder="請選擇模式...",
            options=[
                SelectOption(label="歷史隨機", value="all"),
                SelectOption(label="近四個季度", value="recent"),
                SelectOption(label="當季度", value="current")
            ]
        )
        self.mode_select.callback = self.mode_selected
        self.add_item(self.mode_select)

    async def mode_selected(self, interaction: Interaction):
        selected_mode = self.mode_select.values[0]
        await interaction.response.edit_message(
            content="✅ 模式已選擇，請選擇歌曲數量：",
            view=SongCountSelectorView(self.bot, self.ctx, selected_mode)
        )


# UI：歌曲數量選擇
class SongCountSelectorView(ui.View):
    def __init__(self, bot, ctx, mode):
        super().__init__(timeout=60)
        self.bot = bot
        self.ctx = ctx
        self.mode = mode

        self.count_select = ui.Select(
            placeholder="請選擇歌曲數量...",
            options=[
                SelectOption(label="6 首（測試用）", value="6"),
                SelectOption(label="32 首", value="32"),
                SelectOption(label="64 首", value="64"),
                SelectOption(label="128 首", value="128"),
                SelectOption(label="256 首", value="256"),
                SelectOption(label="512 首", value="512"),
                SelectOption(label="1024 首", value="1024"),
            ]
        )
        self.count_select.callback = self.count_selected
        self.add_item(self.count_select)

    async def count_selected(self, interaction: Interaction):
        count = int(self.count_select.values[0])
        await interaction.response.edit_message(
            content=f"🎮 遊戲建立中...\n模式：`{self.mode}`，歌曲數量：`{count}`",
            view=None
        )

        try:
            songs = self.load_songs(self.mode)
            if not songs:
                await interaction.followup.send("⚠️ 沒有找到符合條件的歌曲資料。")
                return

            if count > len(songs):
                count = len(songs)

            selected_songs = random.sample(songs, count)

            thread = interaction.channel
            if not isinstance(thread, discord.Thread):
                thread = await interaction.channel.create_thread(name="🎵 動漫歌曲比賽", type=discord.ChannelType.public_thread)

            session = MusicGameSession(self.bot, self.ctx, selected_songs, thread)
            active_sessions[thread.id] = session
            await session.start_round()

        except Exception as e:
            await interaction.followup.send(f"❌ 建立比賽失敗：```{e}```")

    def load_songs(self, mode):
        if not SONG_DATA_PATH.exists():
            return []

        with open(SONG_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        all_songs = []

        def extract_songs(entries):
            songs = []
            for anime, info in entries.items():
                for kind in ['op', 'ed']:
                    for song in info.get(kind, []):
                        song_data = {
                            "title": song['title'],
                            "artist": song['artist'],
                            "anime": anime,
                            "type": kind.upper(),
                            "link": song.get("link")
                        }
                        songs.append(song_data)
            return songs

        if mode == "all":
            for season_data in data.values():
                all_songs.extend(extract_songs(season_data))

        elif mode == "recent":
            recent_keys = sorted(data.keys(), reverse=True)[:4]
            for k in recent_keys:
                all_songs.extend(extract_songs(data[k]))

        elif mode == "current":
            latest_key = sorted(data.keys(), reverse=True)[0]
            all_songs.extend(extract_songs(data[latest_key]))

        return all_songs


async def setup(bot):
    await bot.add_cog(MusicGameLauncher(bot))
