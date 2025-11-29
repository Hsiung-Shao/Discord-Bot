@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "NSSM_PATH=D:\codeproject\python\DiscordBot\nssm\nssm.exe"
set "WORK_DIR=D:\codeproject\python\DiscordBot"
set "PYTHON_PATH=C:\Users\jerry\AppData\Local\Programs\Python\Python312\python.exe"
set "BOT_SCRIPT=D:\codeproject\python\DiscordBot\bot.py"

echo 使用 Python: %PYTHON_PATH%
if exist "%PYTHON_PATH%" (
    "%PYTHON_PATH%" --version
) else (
    echo ❌ Python 執行檔不存在: %PYTHON_PATH%
    pause
    exit /b 1
)
echo.

REM 安裝 Discord Bot 作為 Windows 服務
REM 使用引號包裹完整路徑以處理空格
"%NSSM_PATH%" install DiscordBot "%PYTHON_PATH%" "%BOT_SCRIPT%"

REM 設定工作目錄
"%NSSM_PATH%" set DiscordBot AppDirectory "%WORK_DIR%"

REM 設定標準輸出、錯誤輸出 log
"%NSSM_PATH%" set DiscordBot AppStdout "%WORK_DIR%\logs\stdout.log"
"%NSSM_PATH%" set DiscordBot AppStderr "%WORK_DIR%\logs\stderr.log"

REM 設定服務失敗時自動重啟
"%NSSM_PATH%" set DiscordBot AppExit Default Restart
"%NSSM_PATH%" set DiscordBot AppRestartDelay 5000

REM 設定開機自動啟動
"%NSSM_PATH%" set DiscordBot Start SERVICE_AUTO_START

REM 設定服務描述
"%NSSM_PATH%" set DiscordBot Description "Discord Bot Service"

echo.
echo ✅ DiscordBot 服務已建立完成！
echo.
echo 使用的 Python 路徑: %PYTHON_PATH%
echo 工作目錄: %WORK_DIR%
echo.
pause
