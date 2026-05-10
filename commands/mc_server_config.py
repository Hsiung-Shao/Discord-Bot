import os
import json
from dataclasses import dataclass, asdict, field
from typing import Optional
from utils.logger import get_logger

logger = get_logger(__name__, channel="minecraft")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "minecraft_servers.json")
TEMPLATES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "minecraft_server_templates.json")


@dataclass
class MinecraftServerProfile:
    id: str
    name: str
    base_path: str
    start_bat: str
    jar_keyword: str
    host: str
    game_port: int
    rcon_port: int
    rcon_password: str
    world_folder: str = "world"
    auto_backup: bool = True
    startup_timeout: int = 300
    disabled: bool = False

    @property
    def start_bat_path(self) -> str:
        return os.path.join(self.base_path, self.start_bat)

    @property
    def pid_file(self) -> str:
        return os.path.join(self.base_path, f"{self.id}.pid")

    @property
    def world_path(self) -> str:
        return os.path.join(self.base_path, self.world_folder)


def _read_raw() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"servers": []}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"❌ 讀取 Minecraft 伺服器設定失敗：{e}")
        return {"servers": []}


def _write_raw(data: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_servers(include_disabled: bool = False) -> list[MinecraftServerProfile]:
    """讀取所有 Minecraft 伺服器設定。預設不回傳已停用的伺服器。"""
    data = _read_raw()
    servers = []
    for entry in data.get("servers", []):
        try:
            profile = MinecraftServerProfile(**entry)
        except TypeError as e:
            logger.error(f"❌ 伺服器設定格式錯誤（{entry.get('id', 'unknown')}）：{e}")
            continue
        if profile.disabled and not include_disabled:
            continue
        servers.append(profile)
    return servers


def get_server(server_id: str, include_disabled: bool = True) -> Optional[MinecraftServerProfile]:
    for s in load_servers(include_disabled=include_disabled):
        if s.id == server_id:
            return s
    return None


def save_servers(servers: list[MinecraftServerProfile]) -> None:
    """覆寫整份設定檔（保留欄位順序與既有額外欄位由呼叫端自行處理）。"""
    _write_raw({"servers": [asdict(s) for s in servers]})


def add_server(profile: MinecraftServerProfile) -> None:
    """新增一個伺服器設定；id 重複會丟 ValueError。"""
    data = _read_raw()
    if any(s.get("id") == profile.id for s in data.get("servers", [])):
        raise ValueError(f"伺服器 id 已存在：{profile.id}")
    data.setdefault("servers", []).append(asdict(profile))
    _write_raw(data)
    logger.info(f"➕ 新增 Minecraft 伺服器：{profile.id} ({profile.name})")


def remove_server(server_id: str) -> bool:
    """移除指定 id 的伺服器設定;回傳是否真的有刪除東西。"""
    data = _read_raw()
    original = data.get("servers", [])
    remaining = [s for s in original if s.get("id") != server_id]
    if len(remaining) == len(original):
        return False
    data["servers"] = remaining
    _write_raw(data)
    logger.info(f"🗑️ 已移除 Minecraft 伺服器：{server_id}")
    return True


def set_server_disabled(server_id: str, disabled: bool) -> bool:
    """切換伺服器啟用/停用狀態;回傳是否成功。"""
    data = _read_raw()
    found = False
    for s in data.get("servers", []):
        if s.get("id") == server_id:
            s["disabled"] = disabled
            found = True
            break
    if not found:
        return False
    _write_raw(data)
    state = "停用" if disabled else "啟用"
    logger.info(f"⚙️ 伺服器 {server_id} 已{state}")
    return True


def load_templates() -> dict:
    """讀取伺服器模板清單。回傳 dict[template_id, template_dict]。"""
    if not os.path.exists(TEMPLATES_PATH):
        return {}
    try:
        with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {t["id"]: t for t in data.get("templates", [])}
    except Exception as e:
        logger.error(f"❌ 讀取伺服器模板失敗：{e}")
        return {}


def build_profile_from_template(
    template_id: str,
    server_id: str,
    name: str,
    base_path: str,
    overrides: Optional[dict] = None,
) -> MinecraftServerProfile:
    """以模板為基底建立 profile。overrides 會覆蓋模板預設值。"""
    templates = load_templates()
    if template_id not in templates:
        raise ValueError(f"找不到模板：{template_id}（可用：{', '.join(templates.keys()) or '無'}）")

    tpl = dict(templates[template_id])
    tpl.pop("id", None)
    tpl.pop("description", None)

    fields = {
        "id": server_id,
        "name": name,
        "base_path": base_path,
        **tpl,
        **(overrides or {}),
    }
    valid_keys = {f for f in MinecraftServerProfile.__dataclass_fields__.keys()}
    fields = {k: v for k, v in fields.items() if k in valid_keys}
    return MinecraftServerProfile(**fields)
