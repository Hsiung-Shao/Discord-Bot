# Discord Bot 專案文件

多功能 Discord 機器人，整合遊戲新聞爬蟲、社群媒體追蹤、遊戲伺服器管理與自動備份功能。

---

## 📌 專案簡介

這是一個基於 **discord.py** 開發的多功能 Discord 機器人，主要功能包含：

- 🎮 **遊戲伺服器管理** - 透過 Discord 控制 Minecraft 與 7 Days to Die 伺服器
- 📰 **遊戲新聞爬蟲** - 自動追蹤 FF14、Brown Dust 2、Valorant、LoL 等遊戲的最新消息
- 🐦 **社群媒體追蹤** - 使用 Twikit 追蹤 X (Twitter) 帳號的新推文
- 📦 **自動備份系統** - 支援遊戲伺服器存檔的定時備份與壓縮
- 🔄 **訊息轉發** - 跨頻道/伺服器的訊息轉發功能
---

## 🛠️ 技術架構

### 核心技術棧

| 類別 | 技術 |
|------|------|
| **語言** | Python 3.10+ |
| **框架** | discord.py 2.5 (Cog 模組化架構) |
| **排程** | APScheduler |
| **HTTP Client** | aiohttp (異步)、requests (同步) |
| **網頁解析** | BeautifulSoup4 |
| **伺服器通訊** | mcrcon (Minecraft RCON)、mcstatus |
| **Twitter 追蹤** | Twikit 3.11 |
| **系統監控** | psutil |
| **日誌管理** | logging (TimedRotatingFileHandler) |

### 設計模式

- **Cog 模組化設計** - 各功能獨立封裝於 `commands/` 目錄
- **Handler 抽象** - 備份系統採用 Handler Pattern，易於擴充
- **事件驅動** - 利用 discord.py 的 Event 與 Task 機制

---

## 📂 專案結構

```
DiscordBot/
├── bot.py                     # 主程式入口
├── config.py                  # 環境變數與設定管理
├── requirements.txt           # Python 依賴套件
│
├── commands/                  # Discord 指令模組 (Cogs)
│   ├── admin.py               # 管理員指令
│   ├── bdnews.py              # Brown Dust 2 新聞爬蟲
│   ├── commandspanel.py       # 伺服器控制面板 (Embed + Button)
│   ├── ff14news.py            # FF14 新聞爬蟲
│   ├── forwarder.py           # 訊息轉發系統
│   ├── lol.py                 # LoL 新聞爬蟲
│   ├── minecraftserver.py     # Minecraft 伺服器管理
│   ├── riotnews.py            # Riot Games 新聞 (Valorant + LoL)
│   ├── sevendayserver.py      # 7 Days to Die 伺服器管理
│   └── x_tracker.py           # X (Twitter) 推文追蹤
│
├── core/                      # 核心功能模組
│   └── server_manager.py      # 泛用伺服器進程管理類別
│
├── backups/                   # 備份系統
│   ├── manager.py             # 備份管理器
│   ├── base_handler.py        # 備份 Handler 抽象基類
│   ├── minecraft_backup.py    # Minecraft 備份實作
│   └── seven_days_backup.py   # 7 Days to Die 備份實作
│
├── tasks/                     # 排程任務
│   ├── auto_backup_task.py    # 自動備份排程
│   ├── log_compressor.py      # 日誌壓縮排程 (每 7 天壓 .gz,14 天後刪)
│   └── panel_updater.py       # 控制面板狀態更新 (每 5 分鐘)
│
├── utils/                     # 工具模組
│   └── logger.py              # 日誌系統 (每日切檔;新聞/自動化任務只記 WARNING 以上到 bot.log)
│
├── data/                      # 資料存儲
│   ├── news_data.json         # 新聞快取
│   ├── riotnews.json          # Riot 新聞快取
│   ├── ff14news.json          # FF14 新聞快取
│   ├── forwarder_map.json     # 轉發頻道設定
│   └── bdust_remind_users.json # BD2 提醒名單
│
├── logs/                      # 日誌輸出目錄
└── nssm/                      # Windows 服務管理工具
```

---

## 🔧 模組說明

### 遊戲伺服器管理

| 模組 | 說明 |
|------|------|
| `minecraftserver.py` | 透過 RCON 協議控制 MC 伺服器，支援啟動/關閉/狀態監控 |
| `sevendayserver.py` | 透過 Telnet 控制 7DTD 伺服器 |
| `commandspanel.py` | 提供 Discord Embed + Button UI 的圖形化控制面板 |
| `server_manager.py` | 底層伺服器進程管理 (使用 subprocess + psutil) |

### 新聞爬蟲系統

| 模組 | 資料來源 | 特色 |
|------|----------|------|
| `ff14news.py` | FF14 台灣官網 | 支援詳情按鈕展開完整文章 |
| `bdnews.py` | Brown Dust 2 API | 多語言支援、PVP 提醒功能 |
| `riotnews.py` | Valorant / LoL 官網 | 定時檢查改版資訊 |
| `lol.py` | LoL 官網 | Patch Notes 追蹤 |

### 備份系統

採用 **Handler Pattern** 設計：

```
BackupManager
├── MinecraftBackupHandler  # 壓縮 world 目錄
└── SevenDaysBackupHandler  # 壓縮存檔目錄
```

- 支援 **定時自動備份** (透過 APScheduler)
- 自動清理舊備份檔案
- 異步執行避免阻塞主程式

---

## 🚀 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 設定環境變數

複製 `.env.example` 為 `.env`，並填入：

- Discord Bot Token
- 各功能的頻道 ID
- Minecraft / 7DTD 伺服器路徑與密碼
- (選用) Twitter 帳號密碼

### 3. 啟動機器人

```bash
python bot.py
```

### 4. (選用) 註冊為 Windows 服務

```bash
create_discordbot_service.bat
```

---

## 📋 指令列表

### 伺服器管理

- `!startmc` / `!stopmc` - Minecraft 伺服器控制
- `!mccmd` / `!mcconsole` - Minecraft RCON 送指令 / 看 console 輸出
- `!start7d` / `!stop7d` - 7 Days to Die 伺服器控制

> 本機互動 console:雙擊 `tools/mc_console.bat`(即時 tail server log + RCON 下指令,不依賴 bot)
>
> 設定檔:`data/minecraft_servers.json` 可逐台設定關閉倒數 (`shutdown_countdown`) 與
> 開放啟動時段 (`start_window`);`data/panel_config.json` 控制面板顯示內容(改檔即生效)。
> 時段判斷邏輯在 `core/start_window.py`,Minecraft 與 Night of the Dead 共用。

### 新聞功能

- `!fetchnews` - 手動抓取 BD2 新聞
- `!ff14test` - 手動測試 FF14 新聞
- `!remindme` / `!unremindme` - BD2 PVP 提醒訂閱

### Twitter 追蹤

- `!xtrack add <username>` - 新增追蹤
- `!xtrack remove <username>` - 移除追蹤
- `!xtrack list` - 列出追蹤清單

---

## 📄 授權

本專案為私人用途開發。
