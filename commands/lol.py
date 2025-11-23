import discord
import random
import json
import os
from discord.ext import commands
from discord.ui import View, Select

# 以 lol.py 的上一層當作專案根目錄
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project_root
DATA_DIR = os.path.join(BASE_DIR, 'data')

# 各資料檔案路徑（都位於 project_root/data/ 下）
DATA_FILE = os.path.join(DATA_DIR, 'user.json')
THEME_FILE = os.path.join(DATA_DIR, 'lolTheme.json')
HERO_FILE = os.path.join(DATA_DIR, 'lolHero.json')

os.makedirs('./data', exist_ok=True)
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

with open(HERO_FILE, 'r', encoding='utf-8') as f:
    heroList = json.load(f)

class ChallengePaginator(discord.ui.View):
    def __init__(self, challenges, per_page=15):
        super().__init__(timeout=120)  # 視圖有效時間 120 秒
        self.challenges = challenges
        self.per_page = per_page
        self.current_page = 0
        self.max_page = (len(challenges) - 1) // per_page
        self.message = None

    def format_page(self):
        """格式化當前頁面的挑戰內容"""
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_content = "**當前挑戰主題：**\n"
        for i, challenge in enumerate(self.challenges[start:end], start=start + 1):
            page_content += f"{i}. **{challenge['title']}**\n   {challenge['description']}\n"
        return page_content

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.blurple)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_message(interaction)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_page:
            self.current_page += 1
            await self.update_message(interaction)

    @discord.ui.button(label="關閉", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.message.delete()
        self.stop()

    async def update_message(self, interaction: discord.Interaction):
        """更新消息內容"""
        if self.message:
            await self.message.edit(content=self.format_page(), view=self)
            await interaction.response.defer()


class Lol(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.target_message_id = None 

    def read_data(self, path):
        """讀取用戶數據"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def write_data(self, path, data):
        """寫入用戶數據"""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    def generate_teams(self, user_ids):
        """隨機分配隊伍及英雄"""
        random.shuffle(user_ids)
        hero_indices = random.sample(range(len(heroList)), len(user_ids) * 2)
        heroes = [heroList[i] for i in hero_indices]
        teams = {"1": [], "2": []}

        for i, user_id in enumerate(user_ids):
            team_id = "1" if i < len(user_ids) // 2 else "2"
            hero_pair = [heroes[i * 2], heroes[i * 2 + 1]]
            teams[team_id].append({"user": user_id, "result": hero_pair, "team": team_id, "switch_count": 0})

        return teams

    def create_embed(self, team_data):
        """生成嵌入消息"""
        embed = discord.Embed(color=0xFFBD33)
        for team_id, members in team_data.items():
            team_text = "\n".join(
                f'<@{member["user"]}> : {member["result"][0]}, {member["result"][1]}'
                for member in members
            )
            embed.add_field(name=f"**第{team_id}隊**", value=team_text if team_text else "無成員", inline=False)
        return embed

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, data):
        """
        當用戶對某消息添加反應時觸發，將用戶添加到活動數據中。
        """
        # 檢查是否有有效的目標消息 ID
        if not self.target_message_id or data.message_id != self.target_message_id:
            return  # 忽略非目標消息的反應

        # 獲取頻道物件
        channel = self.bot.get_channel(data.channel_id)
        if not channel:
            return

        # 讀取現有數據
        try:
            existing_data = self.read_data(DATA_FILE)
        except (json.JSONDecodeError, FileNotFoundError):
            existing_data = []

        # 檢查用戶是否已存在
        user_id = str(data.user_id)
        if any(entry["user"] == user_id for entry in existing_data):
            # 用戶已存在時提示
            if channel:
                await channel.send(f"<@{user_id}> 已經參加活動！", delete_after=5)
            return

        # 添加新用戶數據
        new_entry = {"user": user_id, "result": ["", ""], "team": ""}
        existing_data.append(new_entry)

        # 寫入數據到 user.json
        self.write_data(DATA_FILE, existing_data)

        # 生成確認嵌入消息
        embed = discord.Embed(title="參與確認", description=f"<@{user_id}> 已成功加入活動！", color=0xFFBD33)

        # 發送嵌入消息到頻道
        if channel:
            await channel.send(embed=embed, delete_after=10)

    async def startlol(self, interaction: discord.Interaction):
        """啟動活動並顯示下拉選單"""
        print("✅ startlol 方法被觸發！")
        class LolSelect(Select):
            def __init__(self, cog: Lol):
                options = [
                    discord.SelectOption(label="初始化參與人數", value="startMatch"),
                    discord.SelectOption(label="隨機分配隊伍", value="randomMatch"),
                    discord.SelectOption(label="顯示當前隊伍", value="show"),
                    discord.SelectOption(label="切換我的英雄", value="switchHero"),
                    discord.SelectOption(label="清除數據", value="clear"),
                ]
                super().__init__(placeholder="選擇功能", options=options)
                self.cog = cog

            async def callback(self, interaction: discord.Interaction):
                if self.values[0] == "startMatch":
                    await self.cog.startMatch(interaction)
                elif self.values[0] == "randomMatch":
                    await self.cog.randomMatch(interaction)
                elif self.values[0] == "show":
                    await self.cog.show(interaction)
                elif self.values[0] == "clear":
                    await self.cog.clear(interaction)
                elif self.values[0] == "switchHero":
                    await self.cog.switch_hero(interaction)

        view = View()
        view.add_item(LolSelect(self))
        await interaction.response.send_message("請選擇功能：", view=view, ephemeral=False)


    @commands.command()
    async def change(self, ctx, item: str):
        """
        重新分配指定隊伍的英雄
        """
        # 讀取現有數據
        user_data = self.read_data(DATA_FILE)
        if not user_data:
            await ctx.send("目前沒有用戶參加活動，請先初始化活動！")
            return

        # 將指定隊伍的用戶篩選出來
        team_users = []
        remaining_data = []
        for row in user_data:
            if row["team"] == item:
                team_users.append(row["user"])
            else:
                remaining_data.append(row)

        if not team_users:
            await ctx.send(f"隊伍 {item} 中沒有用戶！")
            return

        # 隨機生成新英雄分配
        hero_indices = random.sample(range(len(heroList)), len(team_users) * 2)
        heroes = [heroList[i] for i in hero_indices]
        new_team_data = []
        team_description = ""

        for i, user_id in enumerate(team_users):
            hero_pair = [heroes[i * 2], heroes[i * 2 + 1]]
            new_team_data.append({"user": user_id, "result": hero_pair, "team": item})
            team_description += f"<@{user_id}> : {hero_pair[0]}, {hero_pair[1]}\n"

        # 合併數據並寫回文件
        final_data = remaining_data + new_team_data
        self.write_data(DATA_FILE, final_data)

        # 生成嵌入消息
        embed = discord.Embed(color=0xFFBD33)
        embed.add_field(name=f"**第{item}隊**", value=team_description, inline=False)

        # 發送嵌入消息
        await ctx.send(embed=embed)


    async def startMatch(self, interaction):
        """初始化用戶數據"""
        self.write_data(DATA_FILE, [])
        embed = discord.Embed(title="已初始化參與人數", description="請對此消息添加反應以參加活動！", color=0xFFBD33)
        message = await interaction.channel.send(embed=embed)
        # 儲存目標消息 ID
        self.target_message_id = message.id

    async def randomMatch(self, interaction):
        """隨機分配隊伍及英雄"""
        user_data = self.read_data(DATA_FILE)
        user_ids = [row["user"] for row in user_data]
        team_data = self.generate_teams(user_ids)
        self.write_data(DATA_FILE, [member for members in team_data.values() for member in members])
        embed = self.create_embed(team_data)
        await interaction.response.send_message(embed=embed)

    async def switch_hero(self, interaction: discord.Interaction):
        """
        切換發出指令的用戶的英雄，限制每個用戶最多切換兩次
        """
        user_id = str(interaction.user.id)  # 獲取用戶 ID
        user_data = self.read_data(DATA_FILE)  # 讀取現有數據

        # 確認用戶是否存在於活動中
        user_entry = next((entry for entry in user_data if entry["user"] == user_id), None)
        if not user_entry:
            await interaction.response.send_message("你尚未參加活動，無法切換英雄！", ephemeral=True)
            return

        # 初始化或檢查切換次數
        switch_count = user_entry.get("switch_count", 0)
        if switch_count >= 2:
            await interaction.response.send_message("你已經達到英雄切換次數上限（2次）！", ephemeral=True)
            return

        # 獲取新的英雄分配（隨機挑選兩個不同的英雄）
        available_heroes = heroList.copy()
        for entry in user_data:
            if entry["result"]:
                for hero in entry["result"]:
                    if hero in available_heroes:
                        available_heroes.remove(hero)

        if len(available_heroes) < 2:
            await interaction.response.send_message("可用英雄不足，無法完成切換！", ephemeral=True)
            return

        new_heroes = random.sample(available_heroes, 2)

        # 更新用戶的英雄分配
        user_entry["result"] = new_heroes
        user_entry["switch_count"] = switch_count + 1  # 更新切換次數
        self.write_data(DATA_FILE, user_data)  # 保存更新後的數據

        # 發送嵌入消息告知用戶新英雄分配
        embed = discord.Embed(
            title="英雄切換成功！",
            description=f"你的新英雄是：\n1. {new_heroes[0]}\n2. {new_heroes[1]}\n\n"
                        f"你已切換英雄 {user_entry['switch_count']} 次，最多可切換 2 次。",
            color=0x00FF00
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)  # 僅用戶可見



    async def show(self, interaction):
        """顯示當前隊伍分配"""
        user_data = self.read_data(DATA_FILE)
        team_data = {"1": [], "2": []}
        for row in user_data:
            team_data[row["team"]].append(row)
        embed = self.create_embed(team_data)
        await interaction.response.send_message(embed=embed)

    async def clear(self, interaction):
        """清除所有活動數據"""
        self.write_data(DATA_FILE, [])
        await interaction.response.send_message("所有數據已清除！", ephemeral=True)


    @commands.command(name="ac")
    async def add_challenge(self, ctx, title: str, *, description: str):
        """新增挑戰主題"""
        data = self.read_data(THEME_FILE)
        new_challenge = {"title": title, "description": description}
        data["challenges"].append(new_challenge)
        self.write_data(THEME_FILE, data)

        embed = discord.Embed(
            title="新增挑戰成功！",
            description=f"**標題：** {title}\n**描述：** {description}",
            color=0x00FF00
        )
        await ctx.send(embed=embed)

    @commands.command(name="dc")
    async def delete_challenge(self, ctx, title: str):
        """刪除指定挑戰主題"""
        data = self.read_data(THEME_FILE)
        challenges = data["challenges"]
        filtered = [c for c in challenges if c["title"] != title]

        if len(filtered) == len(challenges):
            await ctx.send(f"挑戰主題 `{title}` 不存在，無法刪除。")
            return

        data["challenges"] = filtered
        self.write_data(THEME_FILE, data)

        embed = discord.Embed(
            title="刪除挑戰成功！",
            description=f"已刪除挑戰主題：**{title}**",
            color=0xFF0000
        )
        await ctx.send(embed=embed)

    @commands.command(name="lc")
    async def list_challenges(self, ctx):
        """顯示所有挑戰主題，帶分頁功能"""
        data = self.read_data(THEME_FILE)
        challenges = data.get("challenges", [])

        if not challenges:
            await ctx.send("目前沒有任何挑戰主題！")
            return

        # 初始化分頁視圖
        paginator = ChallengePaginator(challenges)
        paginator.message = await ctx.send(content=paginator.format_page(), view=paginator)



    @commands.command(name="randomChallenge")
    async def random_challenge(self, ctx):
        """隨機選取一個挑戰主題"""
        data = self.read_data(THEME_FILE)
        challenges = data["challenges"]

        if not challenges:
            await ctx.send("目前沒有任何挑戰主題可以選擇！")
            return

        challenge = random.choice(challenges)
        embed = discord.Embed(
            title="隨機挑戰主題",
            description=f"**標題：** {challenge['title']}\n**描述：** {challenge['description']}",
            color=0xADD8E6
        )
        await ctx.send(embed=embed)


async def setup(bot):
    """添加這個Cog到機器人"""
    await bot.add_cog(Lol(bot))
