"""Minecraft server 本機互動 console。

用途
----
Bot 是以 NSSM 跑成 Windows 服務(Session 0),它開的 cmd 視窗在桌面上看不到,
所以改用這支腳本在自己的 cmd 視窗裡:

- **上半部**:即時捲動 `<base_path>/logs/latest.log`(等同原本啟動視窗看到的 console)
- **下半部**:直接打指令,經 RCON 送進伺服器並印出回應

本腳本**完全獨立於 bot**(只讀 `data/minecraft_servers.json` + 連 RCON),
bot 停掉或重啟都不影響,也不會干擾 bot 對伺服器進程的追蹤。

用法
----
    python tools/mc_console.py            # 列出伺服器讓你選
    python tools/mc_console.py cte2       # 直接連指定的伺服器
或直接雙擊 tools/mc_console.bat。
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from typing import Optional

# 讓這支腳本可以直接被雙擊執行(不必先設 PYTHONPATH)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# console 輸出一律走 UTF-8,避免整合包 log 裡的中文在 cmd 變亂碼
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from mcrcon import MCRcon  # noqa: E402

from commands.mc_server_config import MinecraftServerProfile, load_servers  # noqa: E402
from utils.mc_log import follow, tail_lines  # noqa: E402

# === ANSI 色碼(Windows 10+ 的 cmd 需先啟用 VT 模式) ===
RESET, DIM, RED, YELLOW, CYAN, GREEN = (
    "\033[0m", "\033[2m", "\033[31m", "\033[33m", "\033[36m", "\033[32m",
)

HISTORY_LINES = 15  # 進入 console 時先回顧幾行歷史


def _enable_ansi() -> None:
    """在 Windows cmd 啟用 ANSI 跳脫序列(失敗就當作沒有顏色,不影響功能)。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _colorize(line: str) -> str:
    """依 log 等級上色,讓錯誤一眼看得出來。"""
    if "/ERROR]" in line or "/FATAL]" in line:
        return f"{RED}{line}{RESET}"
    if "/WARN]" in line:
        return f"{YELLOW}{line}{RESET}"
    return line


def _rcon(profile: MinecraftServerProfile, command: str) -> str:
    with MCRcon(profile.host, profile.rcon_password, profile.rcon_port) as mcr:
        return mcr.command(command)


def _is_running(profile: MinecraftServerProfile) -> bool:
    """用 RCON 能不能連上來判斷伺服器是否在跑(比掃進程直接,也代表指令送不送得出去)。"""
    try:
        _rcon(profile, "list")
        return True
    except Exception:
        return False


def _pick_server(servers: list[MinecraftServerProfile]) -> Optional[MinecraftServerProfile]:
    print(f"\n{CYAN}可用的 Minecraft 伺服器:{RESET}")
    states = []
    for i, s in enumerate(servers, 1):
        running = _is_running(s)
        states.append(running)
        badge = f"{GREEN}● RUNNING{RESET}" if running else f"{DIM}○ stopped{RESET}"
        print(f"  {i}. {s.name}  [{s.id}]  {badge}")

    # 只有一台在跑就直接選它,省一次輸入
    if states.count(True) == 1:
        chosen = servers[states.index(True)]
        print(f"\n{DIM}→ 自動選擇執行中的 {chosen.name}{RESET}")
        return chosen

    while True:
        try:
            raw = input("\n請輸入編號或 server id(直接 Enter 取消): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(servers):
            return servers[int(raw) - 1]
        for s in servers:
            if s.id == raw:
                return s
        print(f"{RED}找不到「{raw}」,請重新輸入{RESET}")


def _print_help(profile: MinecraftServerProfile) -> None:
    print(f"""
{CYAN}── MC Console 使用說明 ──{RESET}
  直接輸入伺服器指令即可(不用加斜線),例如:
    list                     查看線上玩家
    say 伺服器 10 分鐘後重開    全服廣播
    save-all flush           立即存檔
    time set day             改成白天

  本工具自己的指令(以 / 開頭):
    /status    查看伺服器狀態
    /tail 50   重看最後 50 行 log
    /clear     清空畫面
    /help      顯示這份說明
    /quit      離開 console({YELLOW}不會{RESET}關閉伺服器)

  {DIM}關閉伺服器請用 Discord 的 /stopmc {profile.id}(會確認存檔並清理殘留進程){RESET}
  {DIM}log 捲動時照常打字按 Enter 即可,輸入內容不會遺失{RESET}
""")


def _tail_worker(log_path: str, stop_flag: threading.Event) -> None:
    """背景執行緒:即時印出 server console 的新輸出。"""
    try:
        for line in follow(log_path, from_end=True, stop_flag=stop_flag):
            print(_colorize(line))
    except Exception as e:
        print(f"{RED}[console] log 追蹤中斷:{e}{RESET}")


def _handle_local_command(
    raw: str, profile: MinecraftServerProfile, log_path: str
) -> bool:
    """處理以 / 開頭的本地指令;回傳 False 代表要離開 console。"""
    parts = raw[1:].split()
    cmd = parts[0].lower() if parts else ""

    if cmd in ("quit", "exit", "q"):
        return False
    if cmd == "help":
        _print_help(profile)
    elif cmd == "clear":
        os.system("cls" if os.name == "nt" else "clear")
    elif cmd == "status":
        running = _is_running(profile)
        state = f"{GREEN}● RUNNING{RESET}" if running else f"{DIM}○ stopped{RESET}"
        print(f"  {profile.name} [{profile.id}] {state}")
        print(f"  {DIM}log: {log_path}{RESET}")
        print(f"  {DIM}rcon: {profile.host}:{profile.rcon_port}{RESET}")
    elif cmd == "tail":
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        for line in tail_lines(log_path, min(n, 300)):
            print(_colorize(line))
    else:
        print(f"{YELLOW}未知的本地指令 /{cmd},輸入 /help 看說明{RESET}")
    return True


def _confirm_stop() -> bool:
    print(f"{YELLOW}⚠️  在這裡直接 stop 不會等待存檔確認,也不會清掉殘留的啟動視窗。{RESET}")
    print(f"{YELLOW}   建議改用 Discord 的 /stopmc。仍要送出請輸入 yes:{RESET}")
    try:
        return input("   > ").strip().lower() in ("yes", "y")
    except (EOFError, KeyboardInterrupt):
        return False


def main(argv: list[str]) -> int:
    _enable_ansi()

    servers = load_servers(include_disabled=True)
    if not servers:
        print(f"{RED}❌ data/minecraft_servers.json 裡沒有任何伺服器設定{RESET}")
        return 1

    if len(argv) > 1:
        wanted = argv[1].strip()
        profile = next((s for s in servers if s.id == wanted), None)
        if profile is None:
            print(f"{RED}❌ 找不到伺服器「{wanted}」,可用:{', '.join(s.id for s in servers)}{RESET}")
            return 1
    else:
        profile = _pick_server(servers)
        if profile is None:
            print("已取消")
            return 0

    log_path = profile.console_log_path
    os.system("cls" if os.name == "nt" else "clear")
    print(f"{CYAN}╔══ MC Console ── {profile.name} [{profile.id}]{RESET}")
    print(f"{DIM}║  log : {log_path}{RESET}")
    print(f"{DIM}║  rcon: {profile.host}:{profile.rcon_port}{RESET}")
    print(f"{CYAN}╚══ 輸入 /help 看說明,/quit 離開(不會關閉伺服器){RESET}\n")

    if os.path.exists(log_path):
        print(f"{DIM}── 最後 {HISTORY_LINES} 行 ──{RESET}")
        for line in tail_lines(log_path, HISTORY_LINES):
            print(_colorize(line))
        print(f"{DIM}── 以下為即時輸出 ──{RESET}\n")
    else:
        print(f"{YELLOW}⚠️ 尚未找到 {log_path}(伺服器可能還沒啟動過),將等待它出現{RESET}\n")

    stop_flag = threading.Event()
    tail_thread = threading.Thread(
        target=_tail_worker, args=(log_path, stop_flag), daemon=True
    )
    tail_thread.start()

    try:
        while True:
            try:
                # lstrip BOM:從管線餵指令進來時(如 PowerShell)開頭會多一個
                raw = input("> ").strip().lstrip("﻿").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{DIM}離開 console(不影響伺服器運作){RESET}")
                break

            if not raw:
                continue
            if raw.startswith("/"):
                if not _handle_local_command(raw, profile, log_path):
                    print(f"{DIM}離開 console(不影響伺服器運作){RESET}")
                    break
                continue

            if raw.split()[0].lower() == "stop" and not _confirm_stop():
                print("已取消")
                continue

            try:
                reply = _rcon(profile, raw)
            except Exception as e:
                print(f"{RED}✖ 指令送出失敗({e.__class__.__name__}: {e}){RESET}")
                print(f"{DIM}  伺服器沒在跑、或 RCON 尚未就緒時會這樣{RESET}")
                continue
            reply = (reply or "").strip()
            print(f"{GREEN}< {reply if reply else '(無回應)'}{RESET}")
    finally:
        stop_flag.set()
        tail_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        sys.exit(0)
