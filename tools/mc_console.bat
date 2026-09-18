@echo off
REM ============================================================
REM  Minecraft Server Interactive Console
REM
REM  Live server output + send commands via RCON.
REM  Usage:  double-click            -> pick a server from the list
REM          mc_console.bat cte2     -> connect to a specific server
REM
REM  NOTE: keep this file ASCII-only. cmd.exe parses .bat with the
REM  system ANSI codepage, so UTF-8 CJK here corrupts command lines
REM  (chcp only affects output, not how the .bat itself is parsed).
REM  All Chinese text lives in tools/mc_console.py instead.
REM ============================================================

chcp 65001 >nul
setlocal EnableDelayedExpansion

title MC Console

cd /d "%~dp0.."

REM Same Python as create_discordbot_service.bat; fall back to PATH.
set "PYTHON_PATH=C:\Users\jerry\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_PATH%" set "PYTHON_PATH=python"

"%PYTHON_PATH%" tools\mc_console.py %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo [exit code %EXITCODE%] See the message above for the reason.
    pause
)

endlocal
exit /b %EXITCODE%
