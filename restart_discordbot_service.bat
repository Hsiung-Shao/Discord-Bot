@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "NSSM_PATH=D:\codeproject\python\DiscordBot\nssm\nssm.exe"

title Restart Discord Bot Service

echo.
echo ================================================
echo    Restart Discord Bot Service
echo ================================================
echo.

echo Restarting DiscordBot service...
"%NSSM_PATH%" restart DiscordBot

echo.
echo ================================================
echo    [SUCCESS] DiscordBot service restarted!
echo ================================================
echo.
pause
