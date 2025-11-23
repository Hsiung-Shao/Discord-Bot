# Discord Bot Project

這是一個多功能的 Discord 機器人，整合了多種遊戲新聞爬蟲、社群媒體追蹤、伺服器管理與訊息轉發功能。

## 📋 功能列表

### 📰 新聞爬蟲
- **FF14 新聞**: 自動抓取 FINAL FANTASY XIV 台灣官網的最新消息。
- **Brown Dust 2 新聞**: 追蹤並發送 Brown Dust 2 的最新公告與開發者筆記。
- **Riot Games 新聞**: 支援 Valorant (特戰英豪) 與 League of Legends (英雄聯盟) 的改版資訊更新。

### 🔍 社群追蹤
- **X (Twitter) 追蹤**: 使用 Twikit 追蹤特定帳號的推文，並即時轉發至指定頻道。

### 🎮 伺服器管理
- **Minecraft 伺服器**: 支援透過 Discord 指令啟動/關閉伺服器，並包含狀態監控與自動備份功能。
- **7 Days to Die 伺服器**: 支援透過 Discord 指令管理伺服器狀態與備份。

### ⚙️ 其他功能
- **訊息轉發**: 跨頻道/伺服器訊息轉發系統。
- **控制面板**: 提供圖形化介面 (Embed + Button) 來管理伺服器狀態。

---

## 📖 詳細功能說明

### 1. FF14 新聞爬蟲 (`commands.ff14news`)
自動追蹤 FF14 台灣官網新聞。
- **特色**: 
    - 支援「查看詳情」按鈕，點擊後自動抓取並發送完整文章內容（含圖片）。
    - 自動過濾重複新聞。
- **指令**:
    - `!ff14test`: 手動觸發測試，從所有來源抓取最新 3 筆新聞並發送。

### 2. Brown Dust 2 新聞 (`commands.bdnews`)
追蹤 Brown Dust 2 官方 API 的新聞。
- **特色**:
    - 支援多國語言 API (預設繁體中文)。
    - 自動解析 HTML 內容並以圖文並茂的方式發送。
    - 每週 PVP 結算提醒功能。
- **指令**:
    - `!fetchnews`: 手動觸發新聞抓取。
    - `!remindme`: 加入每週 PVP 結算提醒名單。
    - `!unremindme`: 退出提醒名單。
    - `!listreminders`: 查看提醒名單。

### 3. Riot Games 新聞 (`commands.riotnews`)
追蹤 Valorant 與 LoL 的改版資訊。
- **特色**:
    - 每日定時 (09:00, 20:00) 檢查更新。
    - 自動解析 patch notes 日期，只發送最新的改版資訊。

### 4. X (Twitter) 追蹤 (`commands.x_tracker`)
使用 Twikit 庫模擬瀏覽器行為追蹤推文。
- **指令**:
    - `!xtrack add <username> [channel]`: 新增追蹤帳號。
    - `!xtrack remove <username> [channel]`: 移除追蹤。
    - `!xtrack list`: 列出所有追蹤中的帳號。
    - `!xtrack check`: (管理員) 強制立即檢查更新。

### 5. Minecraft 伺服器管理 (`commands.minecraftserver`)
- **指令**:
    - `!startmc`: 啟動 Minecraft 伺服器。
    - `!stopmc`: 優雅關閉 Minecraft 伺服器 (發送公告 -> 存檔 -> 關閉)。

### 6. 7 Days to Die 伺服器管理 (`commands.sevendayserver`)
- **指令**:
    - `!start7d`: 啟動 7 Days to Die 伺服器。
    - `!stop7d`: 關閉 7 Days to Die 伺服器。

---

## 🛠️ 設定說明 (.env)

請在專案根目錄建立 `.env` 檔案，並填入以下設定：

```env
# Discord Bot Token
BOT_TOKEN=你的BotToken
CONTROL_THREAD_ID=控制面板頻道ID

# FF14 News
FF14_NEWS_THREAD_ID=FF14新聞發送頻道ID
FF14_DATA_FILE=data/ff14news.json

# Brown Dust 2
BDNEWS_THREAD_ID=BD2新聞發送頻道ID
BDUST_REMINDER_CHANNEL_ID=提醒發送頻道ID

# Riot Games
VALORANT_THREAD_ID=特戰英豪新聞頻道ID
LOL_THREAD_ID=英雄聯盟新聞頻道ID
VALORANT_BASE_URL=https://playvalorant.com/zh-tw/news/
LOL_BASE_URL=https://www.leagueoflegends.com/zh-tw/news/

# Twitter/X Tracker
TWITTER_USERNAME=你的Twitter帳號
TWITTER_EMAIL=你的Twitter信箱
TWITTER_PASSWORD=你的Twitter密碼

# Minecraft Server
MINECRAFT_BASE_PATH=伺服器路徑
MINECRAFT_START_BAT=啟動腳本名稱.bat
MINECRAFT_RCON_PORT=25575
MINECRAFT_RCON_PASSWORD=Rcon密碼
MINECRAFT_STATUS_THREAD_ID=狀態監控頻道ID

# 7 Days to Die Server
SEVENDAY_DIR=伺服器路徑
SEVENDAY_EXE=啟動執行檔名稱.exe
SEVENDAY_TELNET_PORT=8081
SEVENDAY_TELNET_PASSWORD=Telnet密碼
SEVENDAY_STATUS_THREAD_ID=狀態監控頻道ID
SEVENDAY_SAVE_PATH=存檔路徑

# Backup
BACKUP_ROOT=備份存放路徑
```

## 🚀 安裝與執行

1.  **安裝 Python 3.10+**
2.  **安裝依賴套件**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **啟動機器人**:
    ```bash
    python bot.py
    ```
