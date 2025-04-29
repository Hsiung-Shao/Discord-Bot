@echo off
REM 必須切換到 NSSM 執行目錄
cd /d F:\Tools

REM 安裝 Discord Bot 作為 Windows 服務
nssm install DiscordBot "C:/Users/jerry/AppData/Local/Programs/Python/Python311/python.exe" "bot.py"

REM 設定工作目錄
nssm set DiscordBot AppDirectory "F:/coding/project/Python/Discord Bot/Discord Bot"

REM 設定標準輸出、錯誤輸出 log
nssm set DiscordBot AppStdout "F:/coding/project/Python/Discord Bot/Discord Bot/stdout.log"
nssm set DiscordBot AppStderr "F:/coding/project/Python/Discord Bot/Discord Bot/stderr.log"

REM 設定開機自動啟動
nssm set DiscordBot Start SERVICE_AUTO_START

echo ✅ DiscordBot 服務已建立完成！
pause
