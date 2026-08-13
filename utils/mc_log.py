"""Minecraft server console log(`<base_path>/logs/latest.log`)的讀取工具。

設計重點
--------
- **編碼自動偵測**:同一台機器上不同整合包的 latest.log 編碼並不一致
  (實測:CTE2 為 cp950、Forge 1.20.1 常駐與 AllTheMons 為 UTF-8),
  所以一律偵測而不寫死;偵測不出來時退回 UTF-8 + errors="replace",確保永遠讀得出東西。
- **反向讀取**:整合包的 latest.log 動輒數百 KB,取尾巴時從檔尾往前讀 bytes,不整檔載入。
- 本模組同時給 Discord cog(commands/minecraftserver.py)與本機互動 console
  (tools/mc_console.py)使用,兩邊行為一致。
"""

from __future__ import annotations

import os
import time
from typing import Iterator, Optional

# 偵測順序:UTF-8 優先(較新的整合包/JDK 21 預設),再試系統 ANSI(舊 Forge)
_CANDIDATE_ENCODINGS = ("utf-8", "cp950")
_FALLBACK_ENCODING = "utf-8"

# 判定「本次關閉有完成存檔」的標記(Vanilla / Forge / NeoForge 通用)
_SHUTDOWN_MARKERS = ("Stopping server", "Stopping the server")
_SAVE_MARKERS = (
    "All dimensions are saved",
    "All chunks are saved",
    "Saving chunks for level",
    "Saved the game",
)

# 掃描關閉標記時往回看的行數
_SHUTDOWN_SCAN_LINES = 300


def console_log_path(base_path: str) -> str:
    """回傳某個 Minecraft server 的 console log 路徑。"""
    return os.path.join(base_path, "logs", "latest.log")


def detect_encoding(path: str, sample_bytes: int = 262144) -> str:
    """偵測 log 檔編碼。

    只取檔尾一段樣本來試解碼(整檔可能很大);為避免樣本邊界剛好切斷多位元組字元
    造成誤判,解碼失敗的位置若落在樣本最後幾個 byte 內則不算失敗。
    偵測不出來回傳 UTF-8(呼叫端一律搭配 errors="replace")。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > sample_bytes:
                f.seek(size - sample_bytes)
            raw = f.read()
    except OSError:
        return _FALLBACK_ENCODING

    if not raw:
        return _FALLBACK_ENCODING

    for enc in _CANDIDATE_ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError as e:
            # 失敗點在樣本尾端 = 多位元組字元被樣本邊界切斷,不算此編碼不符
            if e.start >= len(raw) - 4:
                return enc
        except LookupError:
            continue
    return _FALLBACK_ENCODING


def _decode(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace")


def tail_lines(path: str, n: int = 30, encoding: Optional[str] = None) -> list[str]:
    """取 log 檔最後 n 行(已去除行尾換行);檔案不存在或讀取失敗回傳空 list。

    從檔尾往前分塊讀,直到湊滿 n 行或讀到檔頭。
    """
    if n <= 0 or not os.path.exists(path):
        return []

    enc = encoding or detect_encoding(path)
    chunk_size = 8192
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            # 多讀一行當緩衝,避免第一行被切半
            while pos > 0 and buf.count(b"\n") <= n:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                buf = f.read(read_size) + buf
    except OSError:
        return []

    text = _decode(buf, enc)
    lines = text.splitlines()
    return lines[-n:]


def detect_clean_shutdown(
    path: str, scan_lines: int = _SHUTDOWN_SCAN_LINES
) -> tuple[bool, Optional[str]]:
    """判斷 log 尾段是否顯示「已完成關閉並存檔」。

    回傳 (是否確認存檔, 佐證行)。找不到存檔標記時回傳 (False, None) —
    呼叫端**不得**把 False 當成「關閉失敗」,只代表「無法從 log 佐證存檔完成」。
    """
    lines = tail_lines(path, scan_lines)
    if not lines:
        return False, None

    # 先定位本次關閉的起點,只採計其之後的存檔訊息,避免抓到上一輪的舊紀錄
    start = 0
    for i in range(len(lines) - 1, -1, -1):
        if any(m in lines[i] for m in _SHUTDOWN_MARKERS):
            start = i
            break

    evidence = None
    for line in lines[start:]:
        if any(m in line for m in _SAVE_MARKERS):
            evidence = line.strip()
            # 繼續掃,取最後(也最完整)的那一筆存檔訊息
    return (evidence is not None), evidence


def _file_key(path: str) -> Optional[tuple]:
    """用來判斷檔案是否被換掉(rotate)的識別值。"""
    try:
        st = os.stat(path)
        return (st.st_ino, st.st_dev, st.st_ctime)
    except OSError:
        return None


def follow(
    path: str,
    from_end: bool = True,
    poll_interval: float = 0.4,
    stop_flag=None,
) -> Iterator[str]:
    """持續 tail 一個 log 檔,每有新行就 yield 一行(不含換行)。

    - `from_end=True`:從目前檔尾開始追(不重播歷史)
    - 自動處理檔案被 truncate(size 變小)或 rotate(換成新檔)→ 重新開檔
    - 檔案還不存在時會等待它出現(server 尚未啟動的情況)
    - `stop_flag`:任何有 `is_set()` 的物件(如 threading.Event),設起來就結束迭代
    """

    def _should_stop() -> bool:
        return stop_flag is not None and stop_flag.is_set()

    f = None
    key = None
    enc = _FALLBACK_ENCODING
    pending = b""
    try:
        while not _should_stop():
            if f is None:
                if not os.path.exists(path):
                    time.sleep(poll_interval)
                    continue
                try:
                    enc = detect_encoding(path)
                    f = open(path, "rb")
                    key = _file_key(path)
                    if from_end:
                        f.seek(0, os.SEEK_END)
                    pending = b""
                except OSError:
                    f = None
                    time.sleep(poll_interval)
                    continue

            chunk = f.read(65536)
            if chunk:
                pending += chunk
                # 只輸出完整的行,殘缺的半行留到下一輪
                while b"\n" in pending:
                    raw_line, pending = pending.split(b"\n", 1)
                    yield _decode(raw_line.rstrip(b"\r"), enc)
                continue

            # 沒有新內容:檢查檔案是否被 truncate 或換掉
            try:
                cur_size = os.path.getsize(path)
            except OSError:
                cur_size = -1
            if cur_size >= 0 and (cur_size < f.tell() or _file_key(path) != key):
                f.close()
                f = None
                # rotate 後從新檔開頭讀,才不會漏掉新 server 啟動的前幾行
                from_end = False
                continue

            time.sleep(poll_interval)
    finally:
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
