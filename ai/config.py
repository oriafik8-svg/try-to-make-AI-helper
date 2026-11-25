"""טעינת הגדרות. קובץ config.json (לא נכנס ל-git) + משתני סביבה לסודות."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"


@dataclass
class Config:
    # מיקום לטמפרטורה (ברירת מחדל: תל אביב)
    latitude: float = 32.0853
    longitude: float = 34.7818
    timezone: str = "Asia/Jerusalem"
    # העדפות שהמשתמש בחר. השירותים עצמם עוד לא ממומשים בשלב 1.
    messaging_provider: str = "phone_app"  # "whatsapp" | "phone_app"
    music_provider: str = "spotify"  # "spotify" | "youtube_music"
    # Claude
    model: str | None = None
    claude_bin: str = "claude"
    claude_timeout_s: int = 90
    # Home Assistant: כתובת מקומית. הטוקן רק ממשתנה סביבה HA_TOKEN.
    ha_url: str = "http://homeassistant.local:8123"
    # דומיינים שמחייבים אישור מפורש לפני הפעלה
    ha_confirm_domains: list[str] = field(
        default_factory=lambda: ["lock", "alarm_control_panel", "cover", "water_heater"]
    )
    # פקודת צפצוף לטיימר (ריק = פעמון טרמינל). לדוגמה: ["aplay", "beep.wav"]
    beep_command: list[str] = field(default_factory=list)

    @property
    def ha_token(self) -> str | None:
        return os.environ.get("HA_TOKEN")


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.json"
    cfg = Config()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if hasattr(cfg, key) and key != "ha_token":
                setattr(cfg, key, value)
    if cfg.messaging_provider not in ("whatsapp", "phone_app"):
        raise ValueError("messaging_provider חייב להיות whatsapp או phone_app")
    if cfg.music_provider not in ("spotify", "youtube_music"):
        raise ValueError("music_provider חייב להיות spotify או youtube_music")
    return cfg
