from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

from .storage import (
    APP_DATA_DIR,
    CACHE_DIR,
    DATA_DIR_ENV,
    DEFAULT_DATA_DIR,
    DEFAULT_OUTPUT_DIR,
    SOFTWARE_DIR,
    STORAGE_CONFIG_PATH,
    StorageError,
    configure_data_directory,
    configured_data_directory,
    prepare_storage,
    resolve_output_directory,
)

__all__ = [
    "APP_DATA_DIR",
    "CACHE_DIR",
    "CONFIG_PATH",
    "DATA_DIR_ENV",
    "DEFAULT_DATA_DIR",
    "DEFAULT_OUTPUT_DIR",
    "LIBRARY_DB_PATH",
    "PERSONAL_DB_PATH",
    "SOFTWARE_DIR",
    "STORAGE_CONFIG_PATH",
    "StorageError",
    "configure_data_directory",
    "configured_data_directory",
    "ensure_config_dir",
    "load_config",
    "prepare_storage",
    "resolve_output_directory",
    "save_config",
]  # 保留旧版路径导出，并统一提供便携式存储入口。

logger = logging.getLogger(__name__)
CONFIG_PATH = APP_DATA_DIR / "config.json"  # 兼容旧版本的用户偏好文件。
LIBRARY_DB_PATH = APP_DATA_DIR / "library.sqlite3"  # 可从原始目录重建的作品索引。
PERSONAL_DB_PATH = (
    APP_DATA_DIR / "personal.sqlite3"
)  # 喜欢与收藏夹用户数据，不能按缓存删除。


def ensure_config_dir() -> None:
    """创建本次进程选定的配置目录，不触发迁移或改变活动路径。"""
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
        "output_dir": str(DEFAULT_OUTPUT_DIR),  # GUI 新下载根目录，默认位于软件目录内。
        "cookies_path": "",  # 可选 cookie 文件或 browser=浏览器表达式；离线阅读无需设置。
        "library_roots": [],  # 用户导入的本地根目录，原文件不移动。
        "window_geometry": {"width": 1220, "height": 820},  # 窗口逻辑像素宽高。
    }
