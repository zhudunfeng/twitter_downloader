"""容错读取新旧下载目录，始终以本地原件为准。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .models import LibraryWork, LocalMedia

logger = logging.getLogger(__name__)
IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
)  # 可识别的本地图片扩展名。
VIDEO_SUFFIXES = frozenset(
    {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
)  # 可识别的本地视频扩展名。
MAX_METADATA_BYTES = 8 * 1024 * 1024  # 单份文本元数据的读取上限，避免异常文件占满内存。
_TEMPORARY_MEDIA = re.compile(
    r"\.(?:part|temp|tmp|f\d+)(?:\.|$)", re.IGNORECASE
)  # 下载分片和转码中间文件命名规则。


def _string(value: object) -> str:
    """仅接收真实字符串，避免把异常结构强行当作正文。"""
    return value if isinstance(value, str) else ""


def _number(value: object) -> int | None:
    """读取非负整数字段，并排除布尔值和损坏数据。"""
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def normalize_date(value: object) -> str | None:
    """把合法 ISO 发布时间转换为明确的标准值，未知值保持为空。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat()
    except (ValueError, OverflowError):
        return None


def is_supported_media_filename(filename: str) -> bool:
    """识别可索引的最终媒体文件，排除下载分片和转码中间流。"""
    return (
        Path(filename).suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES
        and _TEMPORARY_MEDIA.search(filename) is None
    )


def _safe_file(directory: Path, filename: str) -> Path | None:
    """只允许作品目录内的相对文件，拒绝父级跳转、盘符和符号链接。"""
    name = filename.replace("\\", "/")
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ":" in name or ".." in relative.parts:
        return None
    candidate = directory.joinpath(*relative.parts)
    try:
        # 不追随目录内部链接，避免导入目录之外的文件。
        current = candidate
        while current != directory:
            if is_link_or_junction(current):
                return None
            current = current.parent
        resolved = candidate.resolve()
        return resolved if resolved.is_relative_to(directory) else None
    except (OSError, RuntimeError, ValueError):
        return None


def is_link_or_junction(path: Path) -> bool:
    """兼容 Python 3.10 检查链接，在支持的平台额外排除目录联接。"""
    junction_check = getattr(path, "is_junction", None)
    return path.is_symlink() or (junction_check is not None and junction_check())


def _file_signature(path: Path) -> tuple[bool, int | None, str]:
    """返回文件可用性和修改标记，文件丢失时不影响其余作品。"""
    try:
        stat = path.stat()
        exists = path.is_file()
        marker = f"{os.path.normcase(str(path))}|{stat.st_mtime_ns}|{stat.st_size}"
        return (
            exists,
            stat.st_size if exists else None,
            hashlib.sha256(marker.encode()).hexdigest(),
        )
    except OSError:
        return False, None, hashlib.sha256(f"missing|{path}".encode()).hexdigest()


def _read_text(directory: Path, filename: str) -> str:
    """限量读取目录内的 UTF-8 文本，支持带 BOM 的历史文件。"""
    path = _safe_file(directory, filename)
    if path is None or not path.is_file():
        return ""
    with path.open("rb") as handle:
        raw = handle.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("元数据文件超过读取上限")
    # 历史 Markdown 在 Windows 上使用 CRLF，按文本语义统一换行后再解析段落。
    return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def _markdown_fields(markdown: str) -> dict[str, Any]:
    """恢复下载器生成的 Markdown 字段，不执行 HTML 或外部资源。"""
    data: dict[str, Any] = {}
    identity = re.search(r"^# tweet (\S+)\s*$", markdown, re.MULTILINE)
    if identity:
        data["tweet_id"] = identity.group(1)
    author = re.search(
        r"^- author: @([^\s]+)(?: \((.*?)\))?\s*$", markdown, re.MULTILINE
    )
    if author:
        data["author"] = {
            "username": author.group(1),
            "display_name": author.group(2) or "",
        }
    for field in ("date", "url"):
        match = re.search(rf"^- {field}: (.+)$", markdown, re.MULTILINE)
        if match:
            data["created_at" if field == "date" else field] = match.group(1).strip()
    text = re.search(
        r"^## text\s*\n(.*?)(?=^## |\Z)", markdown, re.MULTILINE | re.DOTALL
    )
    if text:
        data["text"] = text.group(1).strip("\n")
    article = re.search(r"^## article\s*\n(.*)\Z", markdown, re.MULTILINE | re.DOTALL)
    if article:
        data["article_blocks"] = [{"index": 1, "text": article.group(1).strip()}]
    if not data and markdown.strip():
        data["text"] = markdown.strip()
    return data


def _natural_key(name: str) -> tuple[tuple[int, object], ...]:
    """使第 2 个媒体在第 10 个媒体之前，避免字典序错排。"""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", name)
    )


def _local_media(
    path: Path, media_type: str, width: object = None, height: object = None
) -> LocalMedia:
    """从真实文件状态建立媒体实体，并保留已知尺寸。"""
    exists, size, signature = _file_signature(path)
    return LocalMedia(
        str(path),
        media_type,
        _number(width) or None,
        _number(height) or None,
        exists,
        size,
        signature,
    )


def read_work(directory: str | Path, root: str | Path) -> LibraryWork:
    """读取一个作品目录；元数据损坏时降级为本地正文和媒体。"""
    work_dir = Path(directory).resolve()
    root_dir = Path(root).resolve()
    if not work_dir.is_relative_to(root_dir) or not work_dir.is_dir():
        raise ValueError("作品目录必须位于已选下载根目录内")
    warnings: list[str] = []
    data: dict[str, Any] = {}
    try:
        raw = _read_text(work_dir, "tweet.json")
        payload = json.loads(raw) if raw else None
        if isinstance(payload, dict):
            data = payload
        else:
            warnings.append("缺少有效元数据")
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        logger.warning("作品元数据读取失败，尝试恢复本地内容：%s（%s）", work_dir, exc)
        warnings.append("元数据损坏，已恢复可用本地内容")
    # 仅补回缺失或类型损坏的字段，不覆盖 JSON 中明确保存的空正文和空文章。
    source_author = data.get("author") if isinstance(data.get("author"), dict) else {}
    needs_fallback = (
        not data
        or any(
            field not in data
            for field in ("author", "text", "created_at", "article_blocks")
        )
        or not isinstance(source_author.get("username"), str)
        or not isinstance(data.get("text"), str)
        or bool(
            data.get("created_at") and normalize_date(data.get("created_at")) is None
        )
    )
    if needs_fallback:
        try:
            fallback = _markdown_fields(_read_text(work_dir, "tweet.md"))
            if not fallback:
                plain_text = _read_text(work_dir, "tweet.txt")
                fallback = {"text": plain_text} if plain_text else {}
            restored = False
            for field in ("tweet_id", "text", "url"):
                if not isinstance(data.get(field), str) and field in fallback:
                    data[field] = fallback[field]
                    restored = True
            if (
                normalize_date(data.get("created_at")) is None
                and normalize_date(fallback.get("created_at")) is not None
            ):
                data["created_at"] = fallback["created_at"]
                restored = True
            author_data = (
                data.get("author") if isinstance(data.get("author"), dict) else {}
            )
            fallback_author = (
                fallback.get("author")
                if isinstance(fallback.get("author"), dict)
                else {}
            )
            for field in ("username", "display_name"):
                if (
                    not isinstance(author_data.get(field), str)
                    and field in fallback_author
                ):
                    author_data[field] = fallback_author[field]
                    restored = True
            if author_data:
                data["author"] = author_data
            if (
                not isinstance(data.get("article_blocks"), list)
                and "article_blocks" in fallback
            ):
                data["article_blocks"] = fallback["article_blocks"]
                restored = True
            if restored:
                warnings.append("元数据不完整，已恢复可用本地正文")
        except (OSError, UnicodeError, ValueError):
            warnings.append("正文文件不可读取")
    tweet_id = _string(data.get("tweet_id")) or work_dir.name
    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    username = _string(author.get("username"))
    display_name = _string(author.get("display_name"))
    media: list[LocalMedia] = []
    used_paths: set[str] = set()
    counters = {"image": 0, "video": 0}
    entries = data.get("media")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            warnings.append("部分媒体描述无效")
            continue
        kind = _string(entry.get("type"))
        if kind not in counters:
            warnings.append("部分媒体描述无效")
            continue
        # 历史版本对图片和视频分别编号，必须按类型推进计数器。
        counters[kind] += 1
        legacy_name = (
            f"{tweet_id}_{counters[kind]}.jpg"
            if kind == "image"
            else f"{tweet_id}_video_{counters[kind]}.mp4"
        )
        filename = _string(entry.get("filename")) or legacy_name
        path = (
            _safe_file(work_dir, filename)
            if is_supported_media_filename(filename)
            else None
        )
        if path is None:
            warnings.append("已忽略不安全或未完成的媒体路径")
            placeholder = str(work_dir / f".missing_media_{len(media)}")
            media.append(
                LocalMedia(
                    placeholder,
                    kind,
                    exists=False,
                    signature=f"invalid|{len(media)}|{filename}",
                )
            )
            continue
        canonical = os.path.normcase(str(path))
        if canonical in used_paths:
            continue
        used_paths.add(canonical)
        item = _local_media(path, kind, entry.get("width"), entry.get("height"))
        if not item.exists:
            warnings.append("部分媒体文件缺失")
        media.append(item)
    # 元数据缺失或媒体未记录时，仍展示目录内真实存在的独立媒体文件。
    try:
        files = sorted(work_dir.iterdir(), key=lambda item: _natural_key(item.name))
        for file in files:
            suffix = file.suffix.lower()
            if not is_supported_media_filename(file.name):
                continue
            safe = _safe_file(work_dir, file.name)
            if (
                safe is None
                or os.path.normcase(str(safe)) in used_paths
                or not safe.is_file()
            ):
                continue
            used_paths.add(os.path.normcase(str(safe)))
            media.append(
                _local_media(safe, "image" if suffix in IMAGE_SUFFIXES else "video")
            )
            warnings.append("部分媒体缺少原始顺序，已按文件名恢复")
    except OSError:
        warnings.append("部分媒体目录不可读取")
    blocks = data.get("article_blocks")
    article: list[tuple[int, str]] = []
    for index, block in enumerate(blocks if isinstance(blocks, list) else []):
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            article.append(
                (
                    _number(block.get("index"))
                    if _number(block.get("index")) is not None
                    else index,
                    block["text"],
                )
            )
        elif isinstance(block, str):
            article.append((index, block))
    article.sort(key=lambda value: value[0])
    urls = data.get("article_urls")
    created_at = normalize_date(data.get("created_at"))
    if data.get("created_at") and created_at is None:
        warnings.append("发布日期无法解析")
    text = _string(data.get("text"))
    identity = (
        os.path.normcase(str(root_dir))
        + "|"
        + os.path.normcase(str(work_dir.relative_to(root_dir)))
    )
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    warning = "；".join(dict.fromkeys(warnings))
    version = (
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        + "|"
        + warning
        + "|"
        + "|".join(item.signature for item in media)
    )
    signature = hashlib.sha256(version.encode("utf-8")).hexdigest()
    return LibraryWork(
        key=key,
        root=str(root_dir),
        directory=str(work_dir),
        tweet_id=tweet_id,
        username=username,
        display_name=display_name,
        created_at=created_at,
        text=text,
        article_blocks=tuple(value for _, value in article),
        article_urls=tuple(value for value in urls if isinstance(value, str))
        if isinstance(urls, list)
        else (),
        media=tuple(media),
        url=_string(data.get("url")),
        like_count=_number(data.get("like_count")),
        retweet_count=_number(data.get("retweet_count")),
        reply_count=_number(data.get("reply_count")),
        warning=warning,
        signature=signature,
    )
