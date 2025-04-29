@echo off
REM 切換到 NSSM 目錄
cd /d F:\Tools

REM 重新啟動 DiscordBot 服務
nssm restart DiscordBot

echo 🔄 DiscordBot 服務已重啟！
pause
