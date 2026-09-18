"""分通道(channel)日誌系統。

設計重點
--------
- 預設行為:`get_logger(name)` 仍會寫到 logs/bot.log + console(向後相容)
- 降噪用法:`get_logger(name, level=logging.WARNING)` 同樣寫進 bot.log,但只收
  WARNING 以上;新聞抓取、自動化任務等「正常運作不需要留紀錄」的模組用這個
- 獨立檔用法:`get_logger(name, channel="minecraft")` 會把該 logger 的訊息**只寫到**
  logs/minecraft.log + console,不再 propagate 到 root,避免污染 bot.log。
  目前只有 minecraft / notd / sevenday 三個 channel 在用,因為它們的 log 會在
  伺服器啟動時被 `clear_channel_log(channel)` 清空,當成單次運行紀錄
- 各 log 檔每天午夜切檔(保留 30 份);切檔後的歷史檔由 tasks/log_compressor.py
  每 7 天壓成 .gz、.gz 滿 14 天刪除
- APScheduler 自身的 logger 在這裡壓到 WARNING,避免「Running job / executed
  successfully」每次觸發都灌進 bot.log 與 stderr
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

# APScheduler 每次觸發 job 都會以 INFO 記「Running job」「executed successfully」,
# 這些會經 root 流進 bot.log 與 console(NSSM 的 stderr.log),只留 WARNING 以上
logging.getLogger("apscheduler").setLevel(logging.WARNING)


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


def get_logger(
    name: str,
    *,
    channel: Optional[str] = None,
    level: Optional[int] = None,
) -> logging.Logger:
    """取得 logger。

    - 不傳 channel:行為跟舊版相同 — 訊息進到 root logger,寫進 logs/bot.log + console
    - 傳 level(例如 logging.WARNING):該 logger 只放行此等級以上的訊息;
      搭配不傳 channel 就是「進 bot.log,但只有警告/錯誤才寫」
    - 傳 channel:訊息只寫到 logs/<channel>.log + console,不再 propagate 到 root,
      也就是 bot.log 不會收到這些訊息(用來分離各功能的日誌)
    """
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    if channel is None:
        return logger

    if not getattr(logger, "_channel_setup", False):
        # 此 logger 自己持有 channel file handler + 自己的 console handler
        logger.addHandler(_get_channel_handler(channel))
        console = logging.StreamHandler()
        console.setFormatter(_FORMATTER)
        logger.addHandler(console)
        logger.propagate = False  # 不再寫到 root → 不污染 bot.log
        if level is None:
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
