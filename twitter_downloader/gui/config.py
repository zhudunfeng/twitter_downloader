from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_DATA_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "TwitterDownloader"
CONFIG_PATH = APP_DATA_DIR / "config.json"


def ensure_config_dir() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    ensure_config_dir()
    if not CONFIG_PATH.exists():
        return _defaults()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        defaults = _defaults()
        defaults.update(data if isinstance(data, dict) else {})
        return defaults
    except (OSError, json.JSONDecodeError):
        return _defaults()


def save_config(config: dict[str, Any]) -> None:
    ensure_config_dir()
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _defaults() -> dict[str, Any]:
    return {
        "output_dir": "downloads",
        "cookies_path": "",
        "window_geometry": {"width": 1220, "height": 820},
    }
