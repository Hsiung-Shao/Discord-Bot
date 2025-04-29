@echo off
REM 切換到 NSSM 目錄
cd /d F:\Tools

REM 移除 DiscordBot 服務（confirm 表示不用再次確認）
nssm remove DiscordBot confirm

echo ❌ DiscordBot 服務已移除！
pause
