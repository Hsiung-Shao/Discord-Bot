import discord
from discord.ext import commands
import asyncio
import random

active_sessions = {}

class MusicGameSession:
    def __init__(self, bot, ctx, songs, thread):
        self.bot = bot
        self.ctx = ctx
        self.host_user_id = ctx.author.id
        self.thread = thread
        self.original_songs = songs
        self.current_round = 1
        self.matchups = []
        self.current_match_index = 0
        self.votes = {}  # {user_id: "A" or "B"}
        self.alive_songs = songs.copy()

    def create_matchups(self):
        random.shuffle(self.alive_songs)
        self.matchups = [
            (self.alive_songs[i], self.alive_songs[i + 1])
            for i in range(0, len(self.alive_songs), 2)
        ]
        self.current_match_index = 0
        self.alive_songs = []

    async def start_round(self):
        self.create_matchups()
        await self.next_match()

    async def next_match(self):
        if self.current_match_index >= len(self.matchups):
            if len(self.alive_songs) == 1:
                winner = self.alive_songs[0]
                await self.thread.send(f"🏆 冠軍歌曲為：**{winner['title']}**\n🎶 動畫：{winner['anime']}")
                return
            self.current_round += 1
            await self.start_round()
            return

        song1, song2 = self.matchups[self.current_match_index]
        self.votes = {}

        embed = discord.Embed(
            title=f"🎵 第 {self.current_round} 輪 - 對戰 {self.current_match_index + 1}",
            description="請選擇你喜歡的歌曲！",
            color=discord.Color.blurple()
        )

        def format_song(song):
            lines = [
                f"**動畫：** {song.get('title_localized', '未知')}",
                f"**類型：** {song.get('type', '未知')}",
                f"**歌名：** {song.get('title', '未知')}",
                f"**演唱：** {song.get('singer', '未知')}"
            ]
            if song.get("link"):
                lines.append(f"[🎧 聆聽連結]({song['link']})")
            return "\n".join(lines)

        embed.add_field(name="🎶 A", value=format_song(song1), inline=False)
        embed.add_field(name="🎶 B", value=format_song(song2), inline=False)

        view = VotingView(self, song1, song2)
        await self.thread.send(embed=embed, view=view)


    async def register_vote(self, user: discord.User, choice: str):
        if user.id in self.votes:
            return False
        self.votes[user.id] = choice
        return True

    async def end_current_match(self):
        # ✅ 防止重複結束或超出比賽範圍
        if self.current_match_index >= len(self.matchups):
            return

        song1, song2 = self.matchups[self.current_match_index]
        a_votes = len([v for v in self.votes.values() if v == "A"])
        b_votes = len([v for v in self.votes.values() if v == "B"])

        if a_votes == b_votes == 0:
            winner = random.choice([song1, song2])
            result_text = "⚠️ 沒有投票，自動隨機選出勝者。"
        else:
            winner = song1 if a_votes >= b_votes else song2
            result_text = f"✅ 結果：A={a_votes}票，B={b_votes}票 → 勝者：**{winner['title']}**"

        self.alive_songs.append(winner)
        self.current_match_index += 1
        await self.thread.send(result_text)
        await asyncio.sleep(1)
        await self.next_match()


    async def transfer_host(self, new_user: discord.User):
        self.host_user_id = new_user.id
        await self.thread.send(f"🔄 主持人已轉移給 {new_user.mention}")


class VotingView(discord.ui.View):
    def __init__(self, session: MusicGameSession, song1, song2):
        super().__init__(timeout=300)
        self.session = session
        self.song1 = song1
        self.song2 = song2
        self.timeout_task = asyncio.create_task(self.auto_end())

    async def auto_end(self):
        await asyncio.sleep(300)
        await self.session.end_current_match()

    @discord.ui.button(label="選擇 A", style=discord.ButtonStyle.primary)
    async def vote_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        success = await self.session.register_vote(interaction.user, "A")
        if success:
            await interaction.response.send_message("你選擇了 A", ephemeral=True)
        else:
            await interaction.response.send_message("你已投過票了。", ephemeral=True)

    @discord.ui.button(label="選擇 B", style=discord.ButtonStyle.primary)
    async def vote_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        success = await self.session.register_vote(interaction.user, "B")
        if success:
            await interaction.response.send_message("你選擇了 B", ephemeral=True)
        else:
            await interaction.response.send_message("你已投過票了。", ephemeral=True)

    @discord.ui.button(label="▶️ 提早結束投票", style=discord.ButtonStyle.secondary)
    async def force_end(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.session.host_user_id:
            await interaction.response.send_message("⚠️ 僅主持人可以結束本輪投票。", ephemeral=True)
            return
        await interaction.response.defer()
        await self.session.end_current_match()
