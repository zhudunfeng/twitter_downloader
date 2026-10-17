from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


# 日志格式：时间、级别、模块名、消息
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
# 日志文件名
LOG_FILE_NAME = "twitter_downloader.log"
# 单个日志文件最大字节数（2 MB）
MAX_BYTES = 2 * 1024 * 1024
# 保留的历史日志文件数量
BACKUP_COUNT = 5
# 项目统一使用的根 logger 名称
ROOT_LOGGER_NAME = "twitter_downloader"

# 标记是否已完成初始化，避免重复添加 handler 导致日志重复输出
_configured = False
# 当前生效的日志文件路径
_log_path: Path | None = None
# 控制台 handler 引用，便于切换 verbose 级别
_console_handler: logging.Handler | None = None


def default_log_path() -> Path:
    """返回默认日志文件路径：%APPDATA%\\TwitterDownloader\\logs\\twitter_downloader.log。"""
    # Windows 下使用 APPDATA，其他平台回退到用户配置目录
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    directory = base / "TwitterDownloader" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / LOG_FILE_NAME


def setup_logging(
    verbose: bool = False,
    log_path: str | Path | None = None,
) -> Path:
    """初始化日志：同时输出到控制台与轮转日志文件。

    重复调用是安全的，只会更新日志级别而不会重复添加 handler。
    返回实际使用的日志文件路径。
    """
    global _configured, _log_path, _console_handler

    path = Path(log_path) if log_path else default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_path = path

    root = logging.getLogger(ROOT_LOGGER_NAME)
    # 项目日志统一使用 DEBUG 级别，由各 handler 自行过滤
    root.setLevel(logging.DEBUG)
    # 避免向系统根 logger 冒泡造成重复输出
    root.propagate = False

    if not _configured:
        # 控制台 handler：打包成窗口程序时 stderr 可能为空，此时跳过
        stream = sys.stderr or sys.stdout
        if stream is not None:
            _console_handler = logging.StreamHandler(stream)
            _console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            root.addHandler(_console_handler)

        # 文件 handler：始终记录 DEBUG 全量日志，按大小轮转
        file_handler = RotatingFileHandler(
            path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)
        _configured = True

    set_verbose(verbose)
    return path


def set_verbose(verbose: bool) -> None:
    """切换控制台详细程度：调试模式输出 DEBUG，否则只输出 INFO 及以上。"""
    # 文件 handler 始终保持 DEBUG，这里只调整控制台
    if _console_handler is not None:
        _console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)


def get_log_path() -> Path | None:
    """返回当前日志文件路径；未初始化时返回 None。"""
    return _log_path


def get_logger(name: str) -> logging.Logger:
    """获取项目内子 logger，例如 get_logger("core") -> twitter_downloader.core。"""
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
