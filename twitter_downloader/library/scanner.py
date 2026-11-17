"""可取消的后台作品导入与增量更新。"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from .reader import is_link_or_junction, is_supported_media_filename, read_work
from .repository import LibraryRepository

logger = logging.getLogger(__name__)


class LibraryScanner:
    """把用户选择的下载目录同步到可重建索引。"""

    def __init__(self, repository: LibraryRepository) -> None:
        """使用调用线程拥有的仓库，扫描器不另开共享连接。"""
        self.repository = repository  # 扫描期间使用的同线程作品仓库。

    def scan(
        self,
        roots: list[str],
        on_progress: Callable[[int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        """扫描可用根目录，完整完成后才清理该根目录失效记录。"""
        processed = 0
        visited: set[str] = set()
        for raw_root in roots:
            if cancelled is not None and cancelled():
                break
            try:
                root = Path(raw_root).resolve()
                accessible = root.is_dir()
            except (OSError, ValueError, RuntimeError):
                logger.warning("下载目录无法解析，保留已有索引：%s", raw_root)
                continue
            canonical = os.path.normcase(str(root))
            if canonical in visited:
                continue
            visited.add(canonical)
            if not accessible:
                logger.warning("下载目录暂不可访问，保留已有索引：%s", root)
                continue
            logger.info("开始扫描本地作品：%s", root)
            started_at = time.time()
            seen: set[str] = set()
            complete = True
            errors: list[OSError] = []
            # 不跟随符号链接，读取权限失败会阻止本次清理，避免误删缓存。
            for current, directories, files in os.walk(
                root, followlinks=False, onerror=errors.append
            ):
                if cancelled is not None and cancelled():
                    complete = False
                    break
                parent = Path(current)
                directories[:] = [
                    name
                    for name in directories
                    if not name.startswith(".")
                    and not is_link_or_junction(parent / name)
                ]
                names = set(files)
                has_metadata = bool({"tweet.json", "tweet.md", "tweet.txt"} & names)
                if not (
                    has_metadata
                    or any(is_supported_media_filename(name) for name in files)
                ):
                    continue
                if has_metadata:
                    # 清单中的子目录是同一作品的媒体资产，不再当作额外作品重复导入。
                    directories.clear()
                try:
                    work = read_work(parent, root)
                    self.repository.upsert(work)
                    seen.add(work.key)
                    processed += 1
                    if on_progress is not None and (
                        processed == 1 or processed % 20 == 0
                    ):
                        on_progress(processed, str(parent))
                except (OSError, ValueError) as exc:
                    complete = False
                    logger.warning(
                        "跳过不可读取的作品，保留原索引：%s（%s）", parent, exc
                    )
            if errors:
                complete = False
                logger.warning("部分目录无法读取，本次保留未扫描到的索引：%s", root)
            if cancelled is not None and cancelled():
                complete = False
            if complete:
                removed = self.repository.prune(str(root), seen, started_at=started_at)
                logger.info(
                    "作品目录扫描完成：%s，累计 %d 条，清理失效索引 %d 条",
                    root,
                    processed,
                    removed,
                )
            else:
                logger.info("作品目录扫描已中止或不完整，保留历史索引：%s", root)
            if on_progress is not None:
                on_progress(processed, str(root))
        return processed
