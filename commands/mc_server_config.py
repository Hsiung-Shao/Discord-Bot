import os
import json
from dataclasses import dataclass
from utils.logger import get_logger

logger = get_logger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "minecraft_servers.json")


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
    startup_timeout: int = 300  # 啟動等待 RCON 的最長秒數

    @property
    def start_bat_path(self) -> str:
        return os.path.join(self.base_path, self.start_bat)

    @property
    def pid_file(self) -> str:
        return os.path.join(self.base_path, f"{self.id}.pid")

    @property
    def world_path(self) -> str:
        return os.path.join(self.base_path, self.world_folder)


def load_servers() -> list[MinecraftServerProfile]:
    if not os.path.exists(CONFIG_PATH):
        logger.warning(f"⚠️ Minecraft 伺服器設定檔不存在：{CONFIG_PATH}")
        return []

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"❌ 讀取 Minecraft 伺服器設定失敗：{e}")
        return []

    servers = []
    for entry in data.get("servers", []):
        try:
            servers.append(MinecraftServerProfile(**entry))
        except TypeError as e:
            logger.error(f"❌ 伺服器設定格式錯誤（{entry.get('id', 'unknown')}）：{e}")
    return servers


def get_server(server_id: str) -> MinecraftServerProfile | None:
    for s in load_servers():
        if s.id == server_id:
            return s
    return None
