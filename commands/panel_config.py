"""控制面板顯示設定(`data/panel_config.json`)。

把原本寫死在 `commands/commandspanel.py` 裡的東西搬出來:標題、要顯示哪些遊戲區塊、
伺服器 IP、Radmin VPN 帳密。改 JSON 存檔即生效,**不必重啟 bot**
(面板每次產生時重讀)。

設計重點
--------
- 檔案不存在時**自動寫出一份預設檔**,使用者直接編輯即可,不用自己從零建。
- 讀檔失敗(JSON 壞掉、權限問題)一律退回內建預設並記 log —— 面板是狀態總覽,
  不能因為設定檔打錯字就整個掛掉。
- 形狀比照 `commands/mc_server_config.py`(同專案既有的設定檔模組)。
"""

from __future__ import annotations

import json
import os
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "panel_config.json"
)

# 預設值刻意**不含**真實 IP 與 VPN 帳密 —— 那些是本機環境資訊,只放在
# data/panel_config.json(該目錄已 gitignore)。這裡只留佔位範例,
# 讓第一次跑的人知道格式;真值由使用者自己填進設定檔。
# show_sevendays / show_notd 預設關閉(這兩款目前未啟用)。
DEFAULT_CONFIG: dict[str, Any] = {
    "title": "📊 伺服器狀態總覽",
    "description": "目前的伺服器執行狀況如下：",
    "show_minecraft": True,
    "show_sevendays": False,
    "show_notd": False,
    "show_start_window": True,
    "extra_fields": [
        {
            "name": "🌐 伺服器 IP",
            "value": "`請在 data/panel_config.json 的 extra_fields 填入`",
        },
    ],
}


def _write_default() -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        logger.info(f"🆕 已建立面板設定檔預設值：{CONFIG_PATH}")
    except Exception as e:
        logger.warning(f"⚠️ 建立面板設定檔失敗（不影響面板顯示）：{e}")


def load_panel_config() -> dict[str, Any]:
    """讀取面板設定;檔案不存在會先寫出預設檔。任何失敗都退回內建預設。"""
    if not os.path.exists(CONFIG_PATH):
        _write_default()
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("設定檔最外層必須是物件")
    except Exception as e:
        logger.error(f"❌ 讀取面板設定失敗，改用預設值：{e}")
        return dict(DEFAULT_CONFIG)

    # 缺的鍵補上預設值,使用者只想改一項就不必整份照抄
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)

    fields = merged.get("extra_fields")
    if not isinstance(fields, list):
        logger.warning("⚠️ panel_config.extra_fields 不是陣列，改用預設值")
        merged["extra_fields"] = list(DEFAULT_CONFIG["extra_fields"])
    else:
        # 只留形狀正確的欄位,壞掉的略過而不是整份放棄
        valid = []
        for item in fields:
            if isinstance(item, dict) and item.get("name") and item.get("value"):
                valid.append(item)
            else:
                logger.warning(f"⚠️ panel_config.extra_fields 有無效項目，已略過：{item!r}")
        merged["extra_fields"] = valid

    return merged
