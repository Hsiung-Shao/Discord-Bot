@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "NSSM_PATH=D:\codeproject\python\DiscordBot\nssm\nssm.exe"

title Remove Discord Bot Service

echo.
echo ================================================
echo    Remove Discord Bot Service
echo ================================================
echo.

echo Stopping DiscordBot service...
"%NSSM_PATH%" stop DiscordBot >nul 2>&1

echo Removing DiscordBot service (confirm = no confirmation)...
"%NSSM_PATH%" remove DiscordBot confirm

echo.
echo ================================================
echo    [SUCCESS] DiscordBot service removed!
echo ================================================
echo.
pause
