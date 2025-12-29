import asyncio
import os
import json
import uuid
import logging
import subprocess
import shlex
import time
from datetime import datetime
from collections import Counter
from aiohttp import web
import aiohttp_cors
import discord
from discord.ext import commands, tasks
from config import (
    FEEDBACK_CHANNEL_ID, 
    FEEDBACK_PORT, 
    CLOUDFLARED_PATH, 
    CLOUDFLARED_ARGS,
    FEEDBACK_DATA_FILE
)
from utils.logger import get_logger

logger = get_logger(__name__)

# === 翻譯對照表 ===
TRANS_MAP = {
    "source": {
        "friends": "親友推薦",
        "bahamut": "巴哈姆特",
        "google": "Google 搜尋",
        "discord": "Discord 群組",
        "instagram": "Instagram",
        "threads": "Threads",
        "other": "其他"
    },
    "usageTime": {
        "morning": "上午",
        "afternoon": "下午",
        "evening": "晚上",
        "lateNight": "深夜"
    },
    "usageDuration": {
        "firstTime": "初次使用",
        "oneWeek": "一週內",
        "oneMonth": "一個月內",
        "halfYear": "半年內",
        "yearPlus": "一年以上"
    },
    "feedbackType": {
        "bug": "🐛 錯誤回報",
        "feature": "✨ 功能建議",
        "ui": "🎨 介面體驗",
        "other": "📝 其他"
    }
}

class FeedbackServer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.app = None
        self.runner = None
        self.site = None
        self.tunnel_process = None
        self.log_task = None
        self.feedbacks = []
        self.dashboard_message_id = None # (已啟用雙訊息模式，此變數可能不再準確，僅作保留)
        
        # 載入資料
        self.load_data()

    def load_data(self):
        """從 JSON 載入回饋資料"""
        try:
            if os.path.exists(FEEDBACK_DATA_FILE):
                with open(FEEDBACK_DATA_FILE, 'r', encoding='utf-8') as f:
                    self.feedbacks = json.load(f)
                
                # 資料遷移：補上 ID 與 Status
                modified = False
                for f in self.feedbacks:
                    if 'id' not in f:
                        f['id'] = str(uuid.uuid4())[:8] # 短 ID
                        modified = True
                    if 'status' not in f:
                        f['status'] = 'open'
                        modified = True
                
                if modified:
                    self.save_data()
                    
                logger.info(f"📂 已載入 {len(self.feedbacks)} 筆回饋資料")
            else:
                self.feedbacks = []
                # 確保目錄存在
                os.makedirs(os.path.dirname(FEEDBACK_DATA_FILE), exist_ok=True)
        except Exception as e:
            logger.error(f"❌ 載入回饋資料失敗: {e}")
            self.feedbacks = []

    def save_data(self):
        """儲存回饋資料至 JSON"""
        try:
            with open(FEEDBACK_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.feedbacks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ 儲存回饋資料失敗: {e}")

    async def cog_load(self):
        """Cog 載入時啟動服務"""
        await self.start_web_server()
        if CLOUDFLARED_ARGS:
            await self.start_cloudflared()
        else:
            logger.info("⚠️ 未設定 CLOUDFLARED_ARGS，跳過 Cloudflare Tunnel 啟動")
        
        # 啟動時嘗試更新 Dashboard (稍作延遲等待 Discord 連線)
        self.bot.loop.create_task(self.delayed_dashboard_update())

    async def delayed_dashboard_update(self):
        await self.bot.wait_until_ready()
        await self.update_dashboard()

    async def cog_unload(self):
        await self.stop_cloudflared()
        await self.stop_web_server()

    async def start_web_server(self):
        try:
            self.app = web.Application()
            cors = aiohttp_cors.setup(self.app, defaults={
                "*": aiohttp_cors.ResourceOptions(allow_credentials=True, expose_headers="*", allow_headers="*")
            })
            resource = cors.add(self.app.router.add_resource("/api/feedback"))
            cors.add(resource.add_route("POST", self.handle_feedback))
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, '0.0.0.0', FEEDBACK_PORT)
            await self.site.start()
            logger.info(f"🚀 Feedback Web Server 正在背景運行 (Port: {FEEDBACK_PORT})")
        except Exception as e:
            logger.error(f"❌ 無法啟動 Web Server: {e}")

    async def stop_web_server(self):
        if self.site: await self.site.stop()
        if self.runner: await self.runner.cleanup()

    async def start_cloudflared(self):
        try:
            if os.path.isabs(CLOUDFLARED_PATH):
                exe_path = CLOUDFLARED_PATH
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                possible_path = os.path.join(base_dir, CLOUDFLARED_PATH)
                if os.path.exists(possible_path): exe_path = possible_path
                else:
                    import shutil
                    which_path = shutil.which(CLOUDFLARED_PATH)
                    exe_path = which_path if which_path else os.path.abspath(CLOUDFLARED_PATH)

            if CLOUDFLARED_ARGS and isinstance(CLOUDFLARED_ARGS, str):
               args = shlex.split(CLOUDFLARED_ARGS, posix=False) 
            else: args = []

            logger.info(f"☁️ 正在啟動 Cloudflare Tunnel: {exe_path} {args}")
            if not os.path.exists(exe_path): raise FileNotFoundError(f"找不到檔案: {exe_path}")

            self.tunnel_process = await asyncio.create_subprocess_exec(
                exe_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            self.log_task = asyncio.create_task(self.read_process_logs())
            logger.info(f"✅ Cloudflare Tunnel 已啟動 (PID: {self.tunnel_process.pid})")
        except Exception as e:
            logger.error(f"❌ 無法啟動 Cloudflare Tunnel: {e}")

    async def read_process_logs(self):
        if not self.tunnel_process or not self.tunnel_process.stdout: return
        try:
            while True:
                line = await self.tunnel_process.stdout.readline()
                if not line: break
                decoded_line = line.decode('utf-8', errors='replace').strip()
                if decoded_line: logger.info(f"[Cloudflared] {decoded_line}")
        except Exception as e:
            logger.error(f"❌ 讀取 Cloudflared Log 錯誤: {e}")

    async def stop_cloudflared(self):
        if self.tunnel_process:
            try:
                self.tunnel_process.terminate()
                try: await asyncio.wait_for(self.tunnel_process.wait(), timeout=5.0)
                except asyncio.TimeoutError: self.tunnel_process.kill()
            except Exception as e: logger.error(f"❌ 關閉 Cloudflared 失敗: {e}")
        if self.log_task: self.log_task.cancel()

    def translate(self, category, key):
        """翻譯 Enum 值"""
        return TRANS_MAP.get(category, {}).get(key, key)

    async def handle_feedback(self, request):
        try:
            data = await request.json()
            
            # 加入必要欄位
            data['timestamp'] = datetime.now().isoformat()
            data['id'] = str(uuid.uuid4())[:8]
            data['status'] = 'open'
            
            # 儲存
            self.feedbacks.append(data)
            self.save_data()
            
            # 更新 Dashboard
            await self.update_dashboard()
            
            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"❌ 處理回饋請求失敗: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def update_dashboard(self):
        """更新 Discord 上的儀表板訊息 (雙訊息模式)"""
        channel = self.bot.get_channel(FEEDBACK_CHANNEL_ID)
        if not channel:
            logger.warning(f"⚠️ 找不到回饋頻道 ID: {FEEDBACK_CHANNEL_ID}")
            return

        # 1. 計算/準備資料
        total_count = len(self.feedbacks)
        
        ratings = [float(f.get('rating', 0)) for f in self.feedbacks if isinstance(f.get('rating'), (int, float))]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0

        # NPS 計算
        nps_scores = [int(f.get('npsScore', 0)) for f in self.feedbacks if isinstance(f.get('npsScore'), (int, float))]
        promoters = len([s for s in nps_scores if s >= 9])
        detractors = len([s for s in nps_scores if s <= 6])
        nps = ((promoters - detractors) / len(nps_scores)) * 100 if nps_scores else 0

        # 類型分佈
        types = [self.translate('feedbackType', f.get('feedbackType')) for f in self.feedbacks]
        type_counts = Counter(types)
        type_str = "\n".join([f"{k}: **{v}**" for k, v in type_counts.most_common()])
        
        sources = [self.translate('source', f.get('source')) for f in self.feedbacks]
        source_str = ", ".join([f"{k}({v})" for k, v in Counter(sources).most_common(3)])

        # A. 訊息 1: 統計數據 (Embed)
        stats_embed = discord.Embed(title="📊 使用者回饋儀表板", color=0x2b2d31)
        stats_embed.add_field(name="📈 總回饋數", value=f"**{total_count}**", inline=True)
        stats_embed.add_field(name="⭐ 平均評分", value=f"**{avg_rating:.1f}** / 5.0", inline=True)
        stats_embed.add_field(name="🚀 NPS 指標", value=f"**{nps:.0f}**", inline=True)
        stats_embed.add_field(name="📂 類型分佈", value=type_str if type_str else "無", inline=True)
        stats_embed.add_field(name="🔗 主要來源", value=source_str if source_str else "無", inline=True)
        stats_embed.set_footer(text=f"最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # B. 訊息 2: 列表內容 (Content)
        lines = ["**📝 最新回饋紀錄 (Top 15)**"]
        lines.append("```diff")
        
        if total_count == 0:
            lines.append(" 目前沒有回饋紀錄")
        else:
            recent_feedbacks = sorted(self.feedbacks, key=lambda x: x.get('timestamp', ''), reverse=True)[:15]
            for f in recent_feedbacks:
                f_type = self.translate('feedbackType', f.get('feedbackType'))
                f_content = f.get('content', '').replace('\n', ' ')
                if len(f_content) > 30: f_content = f_content[:30] + "..."
                
                # 狀態前綴邏輯
                is_fixed = f.get('status') == 'fixed'
                
                if is_fixed:
                    prefix = "+" # 綠色
                    if f.get('feedbackType') == 'feature':
                        status_mark = "[✨已實現]"
                    elif f.get('feedbackType') == 'bug':
                        status_mark = "[✅已修復]"
                    else:
                        status_mark = "[✅已完成]"
                else:
                    status_mark = ""
                    # 評分前綴 (若沒修復才看評分)
                    prefix = "+" if f.get('rating', 0) >= 4 else "-" if f.get('rating', 0) <= 2 else " "
                
                lines.append(f"{prefix} [{f_type}]{status_mark} {f_content} ({f.get('rating')}⭐)")
        
        lines.append("```")
        list_content = "\n".join(lines)

        # 3. 發送或編輯訊息
        stats_msg = None
        list_msg = None
        
        async for msg in channel.history(limit=10):
            if msg.author != self.bot.user:
                continue
            
            # 辨識 Stats Msg (有 Embed 且標題吻合)
            if not stats_msg and msg.embeds and msg.embeds[0].title == "📊 使用者回饋儀表板":
                stats_msg = msg
            
            # 辨識 List Msg (Content 包含標題，且沒有 Embed)
            if not list_msg and "**📝 最新回饋紀錄 (Top 15)**" in msg.content and not msg.embeds:
                list_msg = msg
        
        try:
            # 附加 View 到 Stats Message
            view = FeedbackControlView(self)

            if stats_msg and list_msg:
                # 編輯現有
                await stats_msg.edit(embed=stats_embed, view=view)
                await list_msg.edit(content=list_content)
            else:
                # 若缺其一，刪除殘存的 (如果有)
                if stats_msg: await stats_msg.delete()
                if list_msg: await list_msg.delete()
                
                # 發送新的 (順序：先 Stats，後 List)
                await channel.send(embed=stats_embed, view=view)
                await channel.send(content=list_content)
                
        except Exception as e:
            logger.error(f"❌ 更新儀表板失敗: {e}")

# === UI Views ===

class StatusSelect(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        # 篩選未修復的 Bugs 和未實現的 Features
        options = []
        # 按時間倒序
        sorted_feedbacks = sorted(cog.feedbacks, key=lambda x: x.get('timestamp', ''), reverse=True)
        
        for f in sorted_feedbacks:
            # 包含 bug 和 feature
            if f.get('feedbackType') in ['bug', 'feature'] and f.get('status') != 'fixed':
                f_type = "🐛" if f.get('feedbackType') == 'bug' else "✨"
                # 截斷內容
                content_preview = f.get('content', '')[:15]
                label = f"{f_type} {content_preview}..."
                desc = f"{f.get('timestamp', '')[:10]} | {f.get('id')}"
                options.append(discord.SelectOption(label=label, description=desc, value=f.get('id')))
            
            if len(options) >= 25: break
        
        if not options:
            options.append(discord.SelectOption(label="沒有待處理項目", value="none", default=True))
            disabled = True
        else:
            disabled = False
            
        super().__init__(placeholder="選擇已完成/修復的項目...", min_values=1, max_values=1, options=options, disabled=disabled)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.defer()
            return

        fid = self.values[0]
        # 更新狀態
        found = False
        target_f = None
        for f in self.cog.feedbacks:
            if f.get('id') == fid:
                f['status'] = 'fixed'
                f['fixed_at'] = datetime.now().isoformat()
                f['fixed_by'] = interaction.user.name
                found = True
                target_f = f
                break
        
        if found:
            self.cog.save_data()
            await self.cog.update_dashboard()
            
            action_text = "已修復" if target_f.get('feedbackType') == 'bug' else "已實現"
            await interaction.response.send_message(f"✅ 已將 ID `{fid}` 標記為 {action_text}！", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 找不到該回饋項目。", ephemeral=True)

class FeedbackControlView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None) # 此 View 長駐
        self.cog = cog

    @discord.ui.button(label="管理回饋 (更新狀態)", style=discord.ButtonStyle.success, emoji="🛠️", custom_id="manage_feedback_btn")
    async def manage_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 彈出 Select Menu
        view = discord.ui.View()
        view.add_item(StatusSelect(self.cog))
        await interaction.response.send_message("請選擇要標記為「完成」的項目：", view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(FeedbackServer(bot))
