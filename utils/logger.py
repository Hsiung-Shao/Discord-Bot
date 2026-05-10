"""分通道(channel)日誌系統。

設計重點
--------
- 預設行為:`get_logger(name)` 仍會寫到 logs/bot.log + console(向後相容)
- 進階用法:`get_logger(name, channel="minecraft")` 會把該 logger 的訊息**只寫到**
  logs/minecraft.log + console,不再 propagate 到 root,避免污染 bot.log
- 各 channel 的 log 檔每天午夜切檔(保留 30 份);同時保留 `clear_channel_log(channel)`
  讓你在 server 啟動等時機手動清空當前檔
"""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

# === 路徑設定 ===
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log_dir = os.path.join(base_dir, "logs")
os.makedirs(log_dir, exist_ok=True)

_FORMATTER = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _make_file_handler(file_path: str) -> TimedRotatingFileHandler:
    handler = TimedRotatingFileHandler(
        filename=file_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    handler.suffix = "%Y-%m-%d.log"
    handler.setFormatter(_FORMATTER)
    return handler


# === Root logger:只接收沒有 channel 的訊息 ===
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)

# 防止重複 import 時重複加 handler
if not getattr(_root_logger, "_initialized_by_botlogger", False):
    _root_handler = _make_file_handler(os.path.join(log_dir, "bot.log"))
    _root_stream = logging.StreamHandler()
    _root_stream.setFormatter(_FORMATTER)
    _root_logger.addHandler(_root_handler)
    _root_logger.addHandler(_root_stream)
    _root_logger._initialized_by_botlogger = True  # type: ignore[attr-defined]


# === Channel handler 快取 ===
_channel_handlers: dict[str, TimedRotatingFileHandler] = {}


def _get_channel_handler(channel: str) -> TimedRotatingFileHandler:
    if channel not in _channel_handlers:
        _channel_handlers[channel] = _make_file_handler(
            os.path.join(log_dir, f"{channel}.log")
        )
    return _channel_handlers[channel]


def channel_log_path(channel: str) -> str:
    """回傳指定 channel 當前活動的 log 檔絕對路徑(未切檔的版本)。"""
    return os.path.join(log_dir, f"{channel}.log")


def get_logger(name: str, *, channel: Optional[str] = None) -> logging.Logger:
    """取得 logger。

    - 不傳 channel:行為跟舊版相同 — 訊息進到 root logger,寫進 logs/bot.log + console
    - 傳 channel:訊息只寫到 logs/<channel>.log + console,不再 propagate 到 root,
      也就是 bot.log 不會收到這些訊息(用來分離各功能的日誌)
    """
    logger = logging.getLogger(name)
    if channel is None:
        return logger

    if not getattr(logger, "_channel_setup", False):
        # 此 logger 自己持有 channel file handler + 自己的 console handler
        logger.addHandler(_get_channel_handler(channel))
        console = logging.StreamHandler()
        console.setFormatter(_FORMATTER)
        logger.addHandler(console)
        logger.propagate = False  # 不再寫到 root → 不污染 bot.log
        logger.setLevel(logging.INFO)
        logger._channel_setup = True  # type: ignore[attr-defined]
        logger._channel_name = channel  # type: ignore[attr-defined]
    return logger


def clear_channel_log(channel: str) -> bool:
    """清空指定 channel 的當前 log 檔(切檔過的歷史檔不動)。

    用法:在伺服器啟動 / 翻譯任務開始等時機呼叫,讓本次運行的 log 純淨。
    回傳:True 代表成功清空,False 代表檔案不存在或操作失敗。
    """
    file_path = channel_log_path(channel)

    # 先 flush 已有 handler 的 buffer,避免清完又被舊 buffer 寫入
    handler = _channel_handlers.get(channel)
    if handler is not None:
        try:
            handler.flush()
            # 關閉再重開,確保清空後檔案 descriptor 對到新檔
            handler.close()
        except Exception:
            pass

    success = False
    try:
        # 直接 truncate
        with open(file_path, "w", encoding="utf-8"):
            pass
        success = True
    except FileNotFoundError:
        success = False
    except Exception:
        success = False

    # 重建 handler 並 swap 到既有的 logger 們上
    if handler is not None:
        new_handler = _make_file_handler(file_path)
        # 找出所有用過這個 channel 的 logger,把舊 handler 換成新的
        for lg in [logging.getLogger()] + [
            logging.getLogger(n) for n in logging.root.manager.loggerDict
        ]:
            if not isinstance(lg, logging.Logger):
                continue
            if handler in lg.handlers:
                lg.removeHandler(handler)
                lg.addHandler(new_handler)
        _channel_handlers[channel] = new_handler

    return success


# === 既有相容項目 ===
# 之前 utils.logger 在 import 時就讓 root 設好,大量舊程式呼叫 get_logger(__name__) 已正常運作。
# 這個版本完整向後相容,不需要改任何既有檔案,除非你想啟用 channel 分流。
