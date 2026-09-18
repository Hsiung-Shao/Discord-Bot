"""伺服器「開放啟動時段」的共用判斷邏輯(與 Discord 解耦)。

用途
----
限制伺服器只能在特定時段被啟動(例如深夜才開放),授權使用者不受限制。
Minecraft(每台各自設定)與 Night of the Dead 共用同一份實作。

⚠️ 跨午夜與不跨午夜是**兩種不同的判斷式**
--------------------------------------
早期寫法 `h >= from or h < to` 只在跨午夜(如 20→5)時正確;
若設成不跨午夜的區間(如 9→18),該式恆為 True,等於完全沒有限制。
`is_open()` 把三種情況分開處理,不要為了「精簡」再合併回一個式子。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pytz import timezone

DEFAULT_TZ = "Asia/Taipei"

# 給使用者看的時區名稱;沒對照到就直接顯示原字串
_TZ_DISPLAY = {
    "Asia/Taipei": "台北時間",
    "Asia/Tokyo": "東京時間",
    "UTC": "UTC",
}


@dataclass
class StartWindow:
    """開放啟動時段設定。

    - `enabled=False`(預設)代表**不限制**,`is_open()` 恆為 True。
      預設不限制是刻意的:漏設定不該把使用者鎖在門外。
    - `from_hour` 含、`to_hour` 不含,皆為 0-23 的整點。
    - `allowed_user_ids` 裡的人不受時段限制(隨時可啟動)。
    """

    enabled: bool = False
    from_hour: int = 20
    to_hour: int = 5
    tz: str = DEFAULT_TZ
    allowed_user_ids: list[int] = field(default_factory=list)

    def __post_init__(self):
        self.from_hour = int(self.from_hour) % 24
        self.to_hour = int(self.to_hour) % 24
        self.allowed_user_ids = [int(uid) for uid in (self.allowed_user_ids or [])]

    # === 判斷 ===

    def is_open(self, now: Optional[datetime] = None) -> bool:
        """現在(或指定時間)是否在開放時段內。未啟用限制時恆為 True。"""
        if not self.enabled:
            return True

        if now is None:
            now = datetime.now(timezone(self.tz))
        h = now.hour

        if self.from_hour == self.to_hour:
            # 起訖相同 = 全天開放(不是全天關閉;設定失誤不該把所有人擋在外面)
            return True
        if self.from_hour < self.to_hour:
            # 一般區間,如 09:00–18:00
            return self.from_hour <= h < self.to_hour
        # 跨午夜,如 20:00–05:00
        return h >= self.from_hour or h < self.to_hour

    def can_start(self, user_id: Optional[int], now: Optional[datetime] = None) -> bool:
        """啟動權限:授權使用者不限時段;其他人僅限開放時段。"""
        if user_id is not None and int(user_id) in self.allowed_user_ids:
            return True
        return self.is_open(now)

    # === 顯示 ===

    def describe(self) -> str:
        """給面板/說明用的簡短描述。"""
        if not self.enabled:
            return "不限時段"
        return f"{self.from_hour:02d}:00–{self.to_hour:02d}:00"

    @property
    def tz_display(self) -> str:
        return _TZ_DISPLAY.get(self.tz, self.tz)

    def deny_message(self, display_name: str) -> str:
        return (
            f"⛔ 目前非開放啟動時段。{display_name} 僅能於每日 "
            f"{self.from_hour:02d}:00–{self.to_hour:02d}:00（{self.tz_display}）開放啟動，"
            f"其餘時段僅限授權使用者。"
        )

    # === 序列化 ===

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "StartWindow":
        """從設定檔的 dict 建立;`None` 或空 dict 代表不限制。

        未知的鍵一律忽略,設定檔多打字不會讓伺服器整個載入失敗。
        """
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            from_hour=data.get("from_hour", 20),
            to_hour=data.get("to_hour", 5),
            tz=data.get("tz", DEFAULT_TZ),
            allowed_user_ids=data.get("allowed_user_ids", []),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "from_hour": self.from_hour,
            "to_hour": self.to_hour,
            "tz": self.tz,
            "allowed_user_ids": list(self.allowed_user_ids),
        }
