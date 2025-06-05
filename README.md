# Discord Bot – 遊戲資訊推播與 Minecraft、7 Days to Die 伺服器控制

## 專案簡介
這是一個專為個人 Discord 伺服器打造的多功能 Bot。它組合了**遊戲資訊自動更新**與**遊戲伺服器控制台**功能，能自動在 Discord 上推播特定遊戲的最新資訊，同時允許管理員通過 Discord 指令或介面控制 Minecraft 及 7 Days to Die 等遊戲伺服器。

## 功能特色
- 遊戲資訊推播 (Brown Dust 2, LoL, Valorant)
- Minecraft/7 Days to Die 伺服器啟動、關閉、狀態顯示
- 自動備份系統
- 定時提醒與推送
- 訊息轉發模組
- Discord 控制面板加上狀態 Embed 和按鈕
- Cog 管理指令（load, reload, unload, listcogs）

## 安裝與執行
1. **下載專案**
    ```bash
    git clone https://github.com/Hsiung-Shao/Discord-Bot.git
    cd Discord-Bot
    ```

2. **建立 `.env` 檔案**
    請將下列格式儲存為 `.env`。
    ```env
    # Discord Bot 身分與控制項目
    BOT_TOKEN=<您的 Discord Bot Token>
    CONTROL_THREAD_ID=<控制面板討論串的 Channel/Thread ID>
    
    # Minecraft 伺服器設定
    MINECRAFT_BASE_PATH=<Minecraft 伺服器主程式路徑>
    MINECRAFT_START_BAT=<啟動 Minecraft 伺服器的批次檔名稱，例如 start.bat>
    MINECRAFT_JAR_KEYWORD=<用於辨識 Minecraft 進程的關鍵字（Jar 檔名片段）>
    MINECRAFT_RCON_PORT=<Minecraft RCON 埠號，如 25575>
    MINECRAFT_RCON_PASSWORD=<Minecraft RCON 密碼>
    MINECRAFT_STATUS_THREAD_ID=<Minecraft 狀態討論串的 ID>
    
    # 7 Days to Die 伺服器設定
    SEVENDAY_DIR=<7 Days 伺服器執行檔所在目錄>
    SEVENDAY_EXE=<7 Days 伺服器執行檔檔名，例如 7DaysToDieServer.exe>
    SEVENDAY_KEYWORD=<用於辨識 7 Days 進程的關鍵字（執行檔名片段）>
    SEVENDAY_TELNET_PORT=<7 Days Telnet 控制埠號（預設 8081）>
    SEVENDAY_TELNET_PASSWORD=<7 Days Telnet 密碼>
    SEVENDAY_STATUS_THREAD_ID=<7 Days 狀態討論串的 ID>
    SEVENDAY_SAVE_PATH=<7 Days 世界存檔資料夾路徑>
    
    # 資料與備份設定
    BACKUP_ROOT=<備份檔案存放根目錄路徑>
    BDNEWS_THREAD_ID=<Brown Dust 2 新聞公告討論串 ID，如不使用可設為0>
    BDUST_REMINDER_CHANNEL_ID=<每週提醒目標頻道 ID，如不使用可設為0>
    VALORANT_THREAD_ID=<Valorant 新聞推播討論串 ID，如不使用則0>
    LOL_THREAD_ID=<LoL 新聞推播討論串 ID，如不使用則0>

    ```

3. **安裝相依套件**
    ```bash
    pip install -r requirements.txt
    ```
    如果未附上 `requirements.txt`，也可以用下列命令：
    ```bash
    pip install -U discord.py python-dotenv psutil aiohttp requests beautifulsoup4 apscheduler mcstatus mcrcon
    ```

4. **啟動 Bot**
    ```bash
    python bot.py
    ```

## 預設語言
- 預設為 **繁體中文**，不支援多語系
- 時區為 Asia/Taipei

## 目錄結構
```bash
Discord-Bot/
├── bot.py                  # Bot 入口
├── config.py               # 讀取 .env 參數
├── commands/               # 指令與 UI 模組
│   ├── minecraftserver.py
│   ├── sevendayserver.py
│   ├── commandspanel.py
│   ├── bdnews.py
│   ├── riotnews.py
│   ├── forwarder.py
│   └── admin.py
├── backups/                # 備份系統
│   ├── minecraft_backup.py
│   ├── seven_days_backup.py
│   └── manager.py
├── tasks/                  # 背景排程任務
│   ├── auto_backup_task.py
│   ├── panel_updater.py
│   ├── anime_song_scheduler.py
│   └── log_compressor.py
├── fetchers/
│   └── acgsecrets.py
├── utils/
│   └── logger.py
├── data/                   # 數據檔位置
└── logs/                   # 日誌輸出
```

## 授權條款
本專案目前 **未指定 License**，如需展開或商業應用請與作者聯繫。
