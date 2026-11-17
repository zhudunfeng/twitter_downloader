from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
APP_DATA_DIR = (
    Path(os.environ.get("APPDATA") or Path.home() / ".config") / "TwitterDownloader"
)  # 用户配置与可重建索引位置。
CACHE_DIR = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    / "TwitterDownloader"
    / "previews"
)  # 可删除的媒体预览缓存。
CONFIG_PATH = APP_DATA_DIR / "config.json"  # 兼容旧版本的用户偏好文件。
LIBRARY_DB_PATH = APP_DATA_DIR / "library.sqlite3"  # 可从原始目录重建的作品索引。


def ensure_config_dir() -> None:
    """创建当前用户可写的配置目录。"""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """读取配置并补齐默认项，损坏或无法读取时安全回退。"""
    try:
        if not CONFIG_PATH.exists():
            return _defaults()
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        defaults = _defaults()
        defaults.update(data if isinstance(data, dict) else {})
        if not isinstance(defaults.get("library_roots"), list):
            defaults["library_roots"] = []
        if not isinstance(defaults.get("window_geometry"), dict):
            defaults["window_geometry"] = _defaults()["window_geometry"]
        return defaults
    except (OSError, ValueError):
        logger.warning("配置无法读取，使用默认设置")
        return _defaults()


def save_config(config: dict[str, Any]) -> None:
    """原子保存配置，避免异常退出留下半份 JSON。"""
    ensure_config_dir()
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=APP_DATA_DIR, suffix=".tmp", delete=False
        ) as handle:
            temporary = handle.name
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, CONFIG_PATH)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _defaults() -> dict[str, Any]:
    """提供各配置项及其兼容默认值。"""
    return {
        "output_dir": "downloads",  # 新下载的保存根目录。
        "cookies_path": "",  # 可选 cookie 文件或 browser=浏览器表达式；离线阅读无需设置。
        "library_roots": [],  # 用户导入的本地根目录，原文件不移动。
        "window_geometry": {"width": 1220, "height": 820},  # 窗口逻辑像素宽高。
    }
