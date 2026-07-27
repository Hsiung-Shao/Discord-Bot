"""鳴潮(Wuthering Waves)繁中官網公告推送。

資料來源為官方網站背後的 CDN JSON API(非 RSS):
  清單  {CDN_BASE}/ArticleMenu.json?t={unix_ts}
  單篇  {CDN_BASE}/article/{articleId}.json?t={unix_ts}
文章網頁連結為 https://wutheringwaves.kurogames.com/zh-tw/main/news/detail/{articleId}

骨架對齊 ff14news.py(排程/去重/多頻道管理/詳情按鈕),差異:
  - 首次啟動 seed 模式:去重檔不存在時寫入全量 ID、不推送,避免灌爆頻道
  - 清單置頂項(top=1)排最前,新舊判斷一律依 createTime 排序,不信任清單順序
"""

import io
import os
import re
import json
import time
import aiohttp
import asyncio
import discord
from PIL import Image
from bs4 import BeautifulSoup, Tag, NavigableString
from discord.ext import commands
from discord.ui import View, Button, Select
from config import WUWA_DATA_FILE
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from utils.logger import get_logger
from urllib.parse import urljoin

logger = get_logger("WuwaNews", channel="wuwanews")

WUWA_CHANNELS_FILE = "data/wuwanews_channels.json"

CDN_BASE = "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/zh-tw"
DETAIL_URL_TEMPLATE = "https://wutheringwaves.kurogames.com/zh-tw/main/news/detail/{}"

# articleType → 顯示名稱(對齊單篇 JSON 的 articleTypeName)
TYPE_LABELS = {89: "新聞", 90: "公告"}

# None 表示不過濾;要只推特定類型時改成 {90} 之類的集合
ALLOWED_TYPES = None

# 標題關鍵字過濾(官方 API 只分公告/新聞兩大類,卡池/宣傳/桌布全混在公告裡,
# 只能靠標題分流)。排除優先於包含;兩個清單都設 None 則不過濾。
# 目標:只推 版本資訊/Patch Notes、維護公告、活動說明,濾掉卡池與宣傳文。
TITLE_EXCLUDE_KEYWORDS = ["喚取", "檔案公開", "桌布分享"]
TITLE_INCLUDE_KEYWORDS = ["版本", "維護", "更新", "活動", "公告", "預告", "補償", "Patch"]


def _title_allowed(title: str) -> bool:
    if TITLE_EXCLUDE_KEYWORDS and any(kw in title for kw in TITLE_EXCLUDE_KEYWORDS):
        return False
    if TITLE_INCLUDE_KEYWORDS is None:
        return True
    return any(kw.lower() in title.lower() for kw in TITLE_INCLUDE_KEYWORDS)

# 單輪最多推送則數,超出的下一輪續推(去重檔逐則寫入,天然斷點續傳)
MAX_PUSH_PER_RUN = 10

# 長圖切段:官方公告常用 1:7 左右的超長直圖,Discord 會縮成糊掉的小縮圖。
# 高/寬超過 TALL_IMAGE_RATIO 的圖,下載後切成「寬 × SLICE_HEIGHT_RATIO」高的多段
# 逐段上傳(一段一則訊息才會全寬顯示;塞同一則會被排成小拼貼)。
TALL_IMAGE_RATIO = 2.5
SLICE_HEIGHT_RATIO = 1.4
SLICE_OVERLAP_PX = 40      # 段間重疊,避免文字行被攔腰切斷後兩邊都缺
MAX_SLICES = 15

# ✦操控✦ / ●活動介面: 這類官方段落標記行 → 加粗並在前面補空行做視覺區隔
SECTION_MARK_RE = re.compile(r"^[✦●◆★▼■]")

# 啟動/重載時自動清理一次推送頻道內 bot 自己的訊息(常駐選單保留)。
# 一次性功能:清完確認沒問題後,把它改回 False 即可關閉。
AUTO_CLEAR_ON_STARTUP = True

# !wuwalist 下拉選單的公告分類(依標題關鍵字,順序即判斷優先序)
CATEGORY_ORDER = ["版本資訊", "維護公告", "活動", "其他公告"]


def _categorize(title: str) -> str:
    if "維護" in title or "停服" in title:
        return "維護公告"
    if "版本" in title:
        return "版本資訊"
    if "活動" in title:
        return "活動"
    return "其他公告"


class WuwaNews(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.channels_config = self._load_channels_config()

    def _load_channels_config(self):
        """載入頻道配置"""
        if not os.path.exists(WUWA_CHANNELS_FILE):
            default_config = {"channel_ids": []}
            os.makedirs(os.path.dirname(WUWA_CHANNELS_FILE), exist_ok=True)
            with open(WUWA_CHANNELS_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=4)
            return default_config

        try:
            with open(WUWA_CHANNELS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"載入鳴潮頻道配置失敗: {e}")
            return {"channel_ids": []}

    def _save_channels_config(self, config=None):
        """儲存頻道配置"""
        if config is None:
            config = self.channels_config
        os.makedirs(os.path.dirname(WUWA_CHANNELS_FILE), exist_ok=True)
        with open(WUWA_CHANNELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)

    async def cog_load(self):
        self._start_scheduler()
        # bot ready 後確保每個推送頻道都有常駐公告查詢選單
        asyncio.create_task(self._ensure_menus_on_ready())

    def cog_unload(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _start_scheduler(self):
        self.scheduler.add_job(self._fetch_news_task, IntervalTrigger(hours=1))
        self.scheduler.start()
        logger.info("鳴潮公告排程已啟動,每 1 小時檢查一次")

    def _load_sent_news(self):
        if not os.path.exists(WUWA_DATA_FILE):
            return None  # None 表示去重檔不存在 → 觸發 seed 模式
        try:
            with open(WUWA_DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []

    def _save_sent_news(self, news_ids):
        os.makedirs(os.path.dirname(WUWA_DATA_FILE), exist_ok=True)
        with open(WUWA_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(news_ids, f, ensure_ascii=False, indent=4)

    async def _fetch_json(self, url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"抓取失敗 {url}: HTTP {response.status}")
                    return None
                # CDN 可能回 text/plain,必須放寬 content_type 檢查
                return await response.json(content_type=None)

    async def _fetch_article_menu(self):
        """抓取全量清單,回傳標準化 item 列表(已過濾類型、按 createTime 升冪)"""
        url = f"{CDN_BASE}/ArticleMenu.json?t={int(time.time())}"
        data = await self._fetch_json(url)
        if not isinstance(data, list):
            return []

        items = []
        for entry in data:
            article_id = entry.get("articleId")
            if article_id is None:
                continue
            article_type = entry.get("articleType")
            if ALLOWED_TYPES is not None and article_type not in ALLOWED_TYPES:
                continue
            if not _title_allowed(entry.get("articleTitle", "")):
                continue
            items.append({
                'id': str(article_id),
                'title': entry.get("articleTitle", "(無標題)"),
                'url': DETAIL_URL_TEMPLATE.format(article_id),
                'date': entry.get("createTime", ""),
                'type': article_type,
            })

        # 置頂項排最前,不可依清單順序判斷新舊;一律按 createTime 排序(舊→新)
        items.sort(key=lambda x: x['date'])
        return items

    async def _fetch_article_detail(self, article_id):
        """抓取單篇文章 JSON,回傳 articleContent 的 HTML 字串"""
        url = f"{CDN_BASE}/article/{article_id}.json?t={int(time.time())}"
        data = await self._fetch_json(url)
        if not isinstance(data, dict):
            return None
        return data.get("articleContent")

    async def _fetch_news_task(self):
        logger.info("開始鳴潮公告檢查...")
        try:
            items = await self._fetch_article_menu()
        except Exception as e:
            logger.error(f"抓取鳴潮清單失敗: {e}")
            return
        if not items:
            logger.warning("鳴潮清單為空,跳過本輪")
            return

        sent_news = self._load_sent_news()
        if sent_news is None:
            # seed 模式:首次啟動,全量 ID 寫入去重檔、不推送
            self._save_sent_news([item['id'] for item in items])
            logger.info(f"首次啟動 seed 完成,已記錄 {len(items)} 筆既有公告,不推送")
            return

        sent_set = set(sent_news)
        new_items = [item for item in items if item['id'] not in sent_set]
        if not new_items:
            return

        if len(new_items) > MAX_PUSH_PER_RUN:
            logger.info(f"新公告 {len(new_items)} 則超過單輪上限 {MAX_PUSH_PER_RUN},其餘下輪續推")
            new_items = new_items[:MAX_PUSH_PER_RUN]

        for item in new_items:
            try:
                await self.notify_news(item)
                sent_news.append(item['id'])
                self._save_sent_news(sent_news)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"處理鳴潮公告 {item['id']} 失敗: {e}")

    def clean_html_and_extract_images(self, raw_html):
        """把文章 HTML 依 DOM 原始順序轉成 Discord Markdown 文字區塊與圖片。

        - 文字與圖片依文章順序交錯輸出,不再把全部文字擠成一團
        - <strong>/<b> → **粗體**;<li> → 「- 」條列;區塊標籤之間換行
        - ✦操控✦ / ●活動介面: 這類段落標記行自動加粗並在前面補空行
        """
        soup = BeautifulSoup(raw_html, "html.parser")
        parts = []
        lines: list[str] = []
        current: list[str] = []

        BLOCK_TAGS = {"p", "div", "section", "table", "tr", "ul", "ol",
                      "blockquote", "h1", "h2", "h3", "h4", "h5"}

        def end_line():
            lines.append("".join(current).strip())
            current.clear()

        def flush_text():
            if current:
                end_line()
            out: list[str] = []
            for ln in lines:
                if ln == "" and (not out or out[-1] == ""):
                    continue  # 折疊連續空行
                if SECTION_MARK_RE.match(ln) and "**" not in ln:
                    if out and out[-1] != "":
                        out.append("")
                    ln = f"**{ln}**"
                out.append(ln)
            lines.clear()
            text = "\n".join(out).strip()
            if text:
                parts.append({"type": "text", "content": text})

        def add_image(tag):
            src = tag.get("data-src") or tag.get("src") or tag.get("data-original")
            if not src:
                return
            flush_text()
            full_url = urljoin("https://hw-media-cdn-mingchao.kurogame.com", src.strip())
            parts.append({"type": "image", "content": full_url})

        def walk(node):
            for child in node.children:
                if isinstance(child, NavigableString):
                    txt = re.sub(r"\s+", " ", str(child))
                    if txt.strip():
                        current.append(txt)
                elif not isinstance(child, Tag):
                    continue
                elif child.name == "img":
                    add_image(child)
                elif child.name == "br":
                    end_line()
                elif child.name in ("strong", "b"):
                    inner = child.get_text(" ", strip=True)
                    if inner:
                        # 相鄰 <strong> 合併,避免產生 **a****b** 導致 Discord 渲染錯誤
                        if current and current[-1].endswith("**"):
                            current[-1] = current[-1][:-2] + inner + "**"
                        else:
                            current.append(f"**{inner}**")
                    for img in child.find_all("img"):
                        add_image(img)
                elif child.name == "li":
                    end_line()
                    current.append("- ")
                    walk(child)
                    end_line()
                elif child.name in BLOCK_TAGS:
                    end_line()
                    walk(child)
                    end_line()
                else:  # a / span / em 等行內標籤
                    walk(child)

        walk(soup)
        flush_text()
        return parts

    async def _send_long_text(self, channel, text):
        """依 Discord 2000 字上限分段發送,盡量在換行處切開避免句子被腰斬"""
        while len(text) > 1990:
            split_at = text.rfind("\n", 0, 1990)
            if split_at <= 0:
                split_at = 1990
            await channel.send(text[:split_at])
            text = text[split_at:].lstrip("\n")
        if text.strip():
            await channel.send(text)

    def _slice_tall_image(self, img_bytes: bytes) -> list[io.BytesIO] | None:
        """高寬比超過門檻的長圖切成多段 JPEG;非長圖或解析失敗回傳 None(維持原網址發送)"""
        try:
            im = Image.open(io.BytesIO(img_bytes))
            im.load()
        except Exception:
            return None
        if im.width <= 0 or im.height / im.width <= TALL_IMAGE_RATIO:
            return None

        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        slice_h = max(int(im.width * SLICE_HEIGHT_RATIO), 200)
        step = slice_h - SLICE_OVERLAP_PX
        slices = []
        top = 0
        while top < im.height and len(slices) < MAX_SLICES:
            bottom = min(top + slice_h, im.height)
            # 最後剩不到 1/4 段就併入上一段,避免出現一小條尾巴
            if im.height - bottom < slice_h // 4:
                bottom = im.height
            buf = io.BytesIO()
            im.crop((0, top, im.width, bottom)).save(buf, format="JPEG", quality=88)
            buf.seek(0)
            slices.append(buf)
            if bottom >= im.height:
                break
            top += step
        return slices

    async def _send_image_part(self, channel, url):
        """發送文章圖片:長圖切段逐段上傳,一般圖直接貼網址由 Discord 展開"""
        img_bytes = None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
        except Exception as e:
            logger.warning(f"下載圖片失敗 {url}: {e}")

        slices = self._slice_tall_image(img_bytes) if img_bytes else None
        if not slices:
            await channel.send(url)
            return

        for idx, buf in enumerate(slices, 1):
            await channel.send(file=discord.File(buf, filename=f"wuwa_{idx:02d}.jpg"))
            await asyncio.sleep(0.5)

    async def send_news_message(self, channel, item):
        type_label = TYPE_LABELS.get(item.get('type'), str(item.get('type', '')))
        embed = discord.Embed(
            title=item['title'],
            url=item['url'],
            description=f"類型: {type_label}\n發布日期: {item['date']}",
            color=0xE8D8B0
        )

        view = View()
        button = Button(label="查看詳情", style=discord.ButtonStyle.primary, custom_id=f"wuwa_news:{item['id']}")
        view.add_item(button)

        try:
            await channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"發送鳴潮公告失敗: {e}")

    async def notify_news(self, item):
        """推送公告到所有配置的頻道(支援跨伺服器)"""
        channel_ids = self.channels_config.get("channel_ids", [])
        if not channel_ids:
            logger.warning("鳴潮沒有配置任何推送頻道,跳過通知")
            return

        success_count = 0
        for channel_id in channel_ids:
            try:
                channel = await self.bot.fetch_channel(channel_id)
                if channel:
                    await self.send_news_message(channel, item)
                    success_count += 1
                    logger.info(f"鳴潮公告已發送到頻道 {channel_id}")
            except discord.NotFound:
                logger.warning(f"找不到鳴潮頻道 {channel_id}")
            except discord.Forbidden:
                logger.warning(f"無權限存取鳴潮頻道 {channel_id}")
            except Exception as e:
                logger.error(f"發送鳴潮公告到頻道 {channel_id} 失敗: {e}")

        if success_count > 0:
            logger.info(f"鳴潮公告已成功發送到 {success_count}/{len(channel_ids)} 個頻道")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.data or 'custom_id' not in interaction.data:
            return

        custom_id = interaction.data['custom_id']

        # 下拉選單:依類型顯示公告清單(只回給操作者,不洗頻)
        if custom_id == 'wuwa_cat_select':
            await self._handle_category_select(interaction)
            return

        if not custom_id.startswith('wuwa_news:'):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            article_id = custom_id.split(':')[1]
            web_url = DETAIL_URL_TEMPLATE.format(article_id)

            raw_html = await self._fetch_article_detail(article_id)
            if not raw_html:
                await interaction.followup.send("無法獲取文章內容。", ephemeral=True)
                return

            try:
                content_parts = self.clean_html_and_extract_images(raw_html)
            except Exception as e:
                logger.error(f"解析鳴潮文章 {article_id} HTML 失敗: {e}")
                content_parts = []

            target_channel = interaction.channel
            if not target_channel:
                target_channel = self.bot.get_channel(interaction.channel_id)

            await target_channel.send(f"**文章詳情**\n連結: {web_url}")

            for part in content_parts:
                if not part['content'].strip():
                    continue

                if part['type'] == 'text':
                    await self._send_long_text(target_channel, part['content'])
                elif part['type'] == 'image':
                    await self._send_image_part(target_channel, part['content'])

        except Exception as e:
            logger.error(f"處理鳴潮詳情按鈕失敗: {e}")
            await interaction.followup.send(f"發生錯誤: {e}", ephemeral=True)

    async def _clear_bot_messages(self, channel, protected: set) -> int:
        """刪除頻道內 bot 自己發送的訊息(protected 內的訊息 ID 保留),回傳刪除數。

        作法對齊 bot.py initialize_panel 清面板的方式:逐則刪自己的訊息,
        不用 purge — 刪自己的訊息不需要「管理訊息」權限,舊訊息也一體適用。
        """
        deleted = 0
        async for msg in channel.history(limit=None, oldest_first=True):
            if msg.author != self.bot.user or msg.id in protected:
                continue
            try:
                await msg.delete()
                deleted += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning(f"刪除訊息 {msg.id} 失敗: {e}")
        return deleted

    async def _handle_clear_select(self, interaction: discord.Interaction):
        """選單的「清理頻道」項:權限判斷必須用 interaction.user(面板互動的 ctx 不可靠)"""
        channel = interaction.channel
        perms = channel.permissions_for(interaction.user) if interaction.guild else None
        if not perms or not perms.manage_messages:
            await interaction.response.send_message("❌ 你需要本頻道的「管理訊息」權限才能清理。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        protected = set()
        menu_msg_id = self.channels_config.get("menu_message_ids", {}).get(str(channel.id))
        if menu_msg_id:
            protected.add(menu_msg_id)
        if interaction.message:
            protected.add(interaction.message.id)  # 臨時選單(!wuwalist 發的)也不刪自己

        try:
            deleted = await self._clear_bot_messages(channel, protected)
            await interaction.followup.send(f"✅ 已清理本頻道內我發送的 {deleted} 則訊息。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 我沒有本頻道的「管理訊息」權限。", ephemeral=True)
        except Exception as e:
            logger.error(f"選單清理頻道失敗: {e}")
            await interaction.followup.send(f"❌ 清理失敗: {e}", ephemeral=True)

    async def _handle_category_select(self, interaction: discord.Interaction):
        values = interaction.data.get('values') or []
        category = values[0] if values else "全部"

        if category == "__clear__":
            await self._handle_clear_select(interaction)
            await self._reset_menu_selection(interaction)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            items = await self._fetch_article_menu()
            if category != "全部":
                items = [it for it in items if _categorize(it['title']) == category]
            latest = list(reversed(items[-10:]))  # 清單為舊→新,反轉成新→舊顯示

            embed = discord.Embed(title=f"📋 鳴潮公告清單・{category}", color=0xE8D8B0)
            if latest:
                embed.description = "\n".join(
                    f"`{it['date'][:10]}` [{it['title']}]({it['url']})" for it in latest
                )
            else:
                embed.description = "(此類型目前沒有公告)"
            embed.set_footer(text=f"顯示最新 {len(latest)} 筆 / 此類型共 {len(items)} 筆")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"公告清單查詢失敗: {e}")
            await interaction.followup.send(f"❌ 查詢失敗: {e}", ephemeral=True)
        await self._reset_menu_selection(interaction)

    async def _reset_menu_selection(self, interaction: discord.Interaction):
        """換上新的 View 重置下拉選單的選取狀態,讓同一選項可再次選擇"""
        try:
            if interaction.message:
                await interaction.message.edit(view=self._build_menu_view())
        except Exception:
            pass

    def _build_menu_view(self) -> View:
        view = View(timeout=None)
        options = [discord.SelectOption(label="全部", value="全部", emoji="📄")]
        emoji_map = {"版本資訊": "🆕", "維護公告": "🔧", "活動": "🎉", "其他公告": "📌"}
        for cat in CATEGORY_ORDER:
            options.append(discord.SelectOption(label=cat, value=cat, emoji=emoji_map.get(cat)))
        options.append(discord.SelectOption(
            label="清理頻道", value="__clear__", emoji="🧹",
            description="刪除本頻道內 bot 發送的訊息(需管理訊息權限)"))
        select = Select(custom_id="wuwa_cat_select",
                        placeholder="選擇公告類型以查看清單",
                        options=options)
        view.add_item(select)
        return view

    MENU_TEXT = "📋 **鳴潮公告清單查詢**(選擇類型後,清單只會顯示給你)"

    async def _ensure_menus_on_ready(self):
        """bot ready 後,為每個推送頻道補建常駐選單(已存在就不動);
        AUTO_CLEAR_ON_STARTUP 開啟時,先自動清理一次頻道內 bot 自己的訊息"""
        try:
            await self.bot.wait_until_ready()
            for channel_id in list(self.channels_config.get("channel_ids", [])):
                if AUTO_CLEAR_ON_STARTUP:
                    await self._auto_clear_channel(channel_id)
                await self._ensure_menu(channel_id)
        except Exception as e:
            logger.error(f"常駐選單初始化失敗: {e}")

    async def _auto_clear_channel(self, channel_id):
        """啟動時自動清理:刪 bot 自己的訊息即可,不需要任何管理權限"""
        try:
            channel = await self.bot.fetch_channel(channel_id)
        except Exception as e:
            logger.warning(f"自動清理: 無法取得頻道 {channel_id}: {e}")
            return
        protected = set()
        menu_msg_id = self.channels_config.get("menu_message_ids", {}).get(str(channel_id))
        if menu_msg_id:
            protected.add(menu_msg_id)
        try:
            deleted = await self._clear_bot_messages(channel, protected)
            logger.info(f"啟動自動清理: 頻道 {channel_id} 已刪除 {deleted} 則自己的訊息")
        except Exception as e:
            logger.error(f"啟動自動清理失敗 (頻道 {channel_id}): {e}")

    async def _ensure_menu(self, channel_id):
        """確保指定頻道有一則常駐選單訊息;被刪或不存在時補發並記錄訊息 ID"""
        menu_ids = self.channels_config.setdefault("menu_message_ids", {})
        key = str(channel_id)
        try:
            channel = await self.bot.fetch_channel(channel_id)
        except Exception as e:
            logger.warning(f"常駐選單: 無法取得頻道 {channel_id}: {e}")
            return

        msg_id = menu_ids.get(key)
        if msg_id:
            try:
                await channel.fetch_message(msg_id)
                return  # 選單還在,不重發
            except (discord.NotFound, discord.Forbidden):
                pass

        try:
            msg = await channel.send(self.MENU_TEXT, view=self._build_menu_view())
            menu_ids[key] = msg.id
            self._save_channels_config()
            logger.info(f"已在頻道 {channel_id} 建立常駐公告選單")
        except Exception as e:
            logger.error(f"建立常駐選單失敗 (頻道 {channel_id}): {e}")

    @commands.command(name="wuwalist")
    async def wuwalist(self, ctx):
        """發送公告清單查詢選單(下拉選擇類型後,清單只顯示給操作者)"""
        await ctx.send(self.MENU_TEXT, view=self._build_menu_view())

    @commands.command(name="wuwaclear")
    @commands.has_permissions(manage_messages=True)
    async def wuwaclear(self, ctx, channel_input=None):
        """清理頻道內 bot 自己發送的訊息(常駐選單會保留)。用法: !wuwaclear [頻道/頻道ID](預設當前頻道)"""
        try:
            target = await self._resolve_channel(ctx, channel_input)
        except Exception as e:
            await ctx.send(f"❌ 無法解析頻道:{e}")
            return

        status_msg = await ctx.send(f"🧹 開始清理 <#{target.id}> 內我發送的訊息...")

        protected = {status_msg.id}
        menu_msg_id = self.channels_config.get("menu_message_ids", {}).get(str(target.id))
        if menu_msg_id:
            protected.add(menu_msg_id)

        try:
            deleted = await self._clear_bot_messages(target, protected)
        except discord.Forbidden:
            await ctx.send("❌ 我沒有該頻道的「管理訊息」權限。")
            return

        done_text = f"✅ 已清理 <#{target.id}> 內我發送的 {deleted} 則訊息。"
        try:
            await status_msg.edit(content=done_text)
        except discord.NotFound:
            await ctx.send(done_text)

    @commands.command(name="wuwatest")
    async def wuwatest(self, ctx, count: int = 3):
        """測試推送最新幾則鳴潮公告到當前頻道。用法: !wuwatest [則數]"""
        await ctx.send("抓取鳴潮最新公告中...")
        try:
            items = await self._fetch_article_menu()
            if not items:
                await ctx.send("沒有抓到任何公告。")
                return

            top_items = items[-count:]  # 清單已為舊→新,取最後幾筆即最新
            await ctx.send(f"共 {len(items)} 則公告,推送最新 {len(top_items)} 則...")

            for item in top_items:
                await self.send_news_message(ctx, item)
                await asyncio.sleep(1)
        except Exception as e:
            await ctx.send(f"測試失敗: {e}")
            logger.error(f"鳴潮測試推送失敗: {e}")

    @commands.group(name="wuwachannels", invoke_without_command=True)
    async def wuwachannels(self, ctx):
        """鳴潮公告推送頻道管理"""
        await ctx.send_help(ctx.command)

    async def _resolve_channel(self, ctx, channel_input):
        """解析頻道參數,支援同伺服器頻道和跨伺服器頻道 ID"""
        if channel_input is None:
            return ctx.channel

        if isinstance(channel_input, discord.TextChannel):
            return channel_input

        try:
            channel_id = int(str(channel_input))
            channel = await self.bot.fetch_channel(channel_id)
            return channel
        except (ValueError, discord.NotFound, discord.Forbidden):
            try:
                converter = commands.TextChannelConverter()
                return await converter.convert(ctx, str(channel_input))
            except:
                raise commands.BadArgument(f"無法解析頻道：{channel_input}")

    @wuwachannels.command(name="add")
    async def add_channel(self, ctx, channel_input=None):
        """新增推送頻道。用法: !wuwachannels add [頻道/頻道ID]"""
        try:
            target_channel = await self._resolve_channel(ctx, channel_input)
        except Exception as e:
            await ctx.send(f"❌ 無法解析頻道：{e}")
            return

        channel_ids = self.channels_config.get("channel_ids", [])

        if target_channel.id in channel_ids:
            channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
            await ctx.send(f"ℹ️ {channel_mention} 已經在推送列表中了。")
            return

        channel_ids.append(target_channel.id)
        self.channels_config["channel_ids"] = channel_ids
        self._save_channels_config()
        await self._ensure_menu(target_channel.id)
        channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
        await ctx.send(f"✅ 已將 {channel_mention} 加入鳴潮公告推送列表,並建立常駐查詢選單。")

    @wuwachannels.command(name="remove")
    async def remove_channel(self, ctx, channel_input=None):
        """移除推送頻道。用法: !wuwachannels remove [頻道/頻道ID]"""
        try:
            target_channel = await self._resolve_channel(ctx, channel_input)
        except Exception as e:
            await ctx.send(f"❌ 無法解析頻道：{e}")
            return

        channel_ids = self.channels_config.get("channel_ids", [])

        if target_channel.id not in channel_ids:
            channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
            await ctx.send(f"ℹ️ {channel_mention} 不在推送列表中。")
            return

        channel_ids.remove(target_channel.id)
        self.channels_config["channel_ids"] = channel_ids

        # 一併撤掉該頻道的常駐選單
        menu_ids = self.channels_config.setdefault("menu_message_ids", {})
        menu_msg_id = menu_ids.pop(str(target_channel.id), None)
        if menu_msg_id:
            try:
                menu_channel = await self.bot.fetch_channel(target_channel.id)
                menu_msg = await menu_channel.fetch_message(menu_msg_id)
                await menu_msg.delete()
            except Exception:
                pass

        self._save_channels_config()
        channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
        await ctx.send(f"✅ 已從鳴潮公告推送列表中移除 {channel_mention}。")

    @wuwachannels.command(name="list")
    async def list_channels(self, ctx):
        """列出所有推送頻道"""
        channel_ids = self.channels_config.get("channel_ids", [])

        if not channel_ids:
            await ctx.send("📭 目前沒有配置任何鳴潮公告推送頻道。")
            return

        embed = discord.Embed(title="鳴潮公告推送頻道列表", color=discord.Color.gold())
        channels = [f"<#{cid}>" for cid in channel_ids]
        embed.description = "\n".join(channels) if channels else "無"
        await ctx.send(embed=embed)

    @wuwachannels.command(name="test")
    async def test_push(self, ctx, channel: discord.TextChannel = None):
        """測試推送功能到指定頻道（或所有配置頻道）。用法: !wuwachannels test [頻道]"""
        test_item = {
            'id': 'TEST',
            'title': '🧪 鳴潮公告推送測試',
            'url': 'https://wutheringwaves.kurogames.com/zh-tw/main/news',
            'date': '測試日期',
            'type': 90,
        }

        if channel:
            try:
                target_channel = await self.bot.fetch_channel(channel.id) if hasattr(channel, 'id') else channel
                await self.send_news_message(target_channel, test_item)
                await ctx.send(f"✅ 測試訊息已發送到 {channel.mention}")
            except Exception as e:
                await ctx.send(f"❌ 測試發送失敗: {e}")
                logger.error(f"鳴潮測試推送失敗: {e}")
        else:
            channel_ids = self.channels_config.get("channel_ids", [])
            if not channel_ids:
                await ctx.send("❌ 沒有配置任何推送頻道，請先使用 `!wuwachannels add` 添加頻道")
                return

            success_count = 0
            failed_count = 0
            for channel_id in channel_ids:
                try:
                    target_channel = await self.bot.fetch_channel(channel_id)
                    await self.send_news_message(target_channel, test_item)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"鳴潮測試推送失敗 (頻道 {channel_id}): {e}")

            await ctx.send(f"✅ 測試完成！成功: {success_count}，失敗: {failed_count}")


async def setup(bot):
    await bot.add_cog(WuwaNews(bot))
