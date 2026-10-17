from __future__ import annotations

import io
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterator, List, Tuple

import requests
from PIL import Image

_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:twitter\.com|x\.com)/[^/]+/status/\d+(?:/[^/?#]+)*(?:[?#].*)?$"
)


def sanitize_filename(name: str) -> str:
    value = unicodedata.normalize("NFKD", name)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\-. ]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    return value.strip("_") or "output"


def is_tweet_url(value: str) -> bool:
    return bool(_URL_RE.match(value.strip()))


class _SilentLogger:
    """yt-dlp 的静默日志器，用于探测浏览器 cookie 时不向终端输出任何内容。"""

    def debug(self, message: str) -> None:
        # 静默丢弃调试日志
        pass

    def info(self, message: str) -> None:
        # 静默丢弃普通日志
        pass

    def warning(self, message: str) -> None:
        # 静默丢弃警告日志
        pass

    def error(self, message: str) -> None:
        # 静默丢弃错误日志
        pass


def cookie_sources(cookies: str | None = None) -> Iterator[str]:
    """按优先级惰性产出可用的 cookie 来源。

    顺序为：显式传入的 cookie → 应用配置目录下的 cookies.txt →
    当前工作目录下的 cookies.txt → 已登录 X 的浏览器。
    """
    # 显式指定了 cookie（文件路径或 browser=xxx）时直接返回，不再自动探测
    if cookies and cookies.strip().lower() not in {"", "auto"}:
        yield cookies.strip()
        return

    # 依次尝试约定位置的 cookie 文件
    for candidate in _cookie_file_candidates():
        if candidate.is_file():
            yield str(candidate)

    import yt_dlp

    # 逐个浏览器检查是否存在已登录的 X 会话
    for browser in ("chrome", "edge", "firefox", "brave", "chromium", "vivaldi", "opera"):
        try:
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "no_warnings": True,
                "logger": _SilentLogger(),
                "cookiesfrombrowser": (browser,),
            }) as ydl:
                # 只有存在 auth_token/ct0 才算真正登录，游客 cookie 无效
                has_session = any(
                    cookie.name in {"auth_token", "ct0"}
                    and any(domain in (cookie.domain or "") for domain in ("x.com", "twitter.com"))
                    for cookie in ydl.cookiejar
                )
        except Exception:
            # 浏览器未安装、cookie 库被占用或无法解密时跳过该浏览器
            continue
        if has_session:
            # 命中登录态，交给 yt-dlp 以 browser= 形式读取
            yield f"browser={browser}"


def _cookie_file_candidates() -> list[Path]:
    """返回约定位置的 cookie 文件候选路径，应用配置目录优先于当前目录。"""
    # 应用配置目录在 Windows 下为 %APPDATA%\TwitterDownloader\cookies.txt
    appdata = os.environ.get("APPDATA")
    candidates = [Path.cwd() / "cookies.txt"]
    if appdata:
        candidates.insert(0, Path(appdata) / "TwitterDownloader" / "cookies.txt")
    return candidates


def ensure_dir(path: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def download_image_to_jpg(url: str, dest_path: str, timeout: int = 30) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.content
    with Image.open(io.BytesIO(data)) as img:
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = background
        else:
            img = img.convert("RGB")
        img.save(dest_path, "JPEG", quality=95)
    return dest_path


def download_files(urls: List[str], dest_paths: List[str], max_workers: int = 8) -> List[str]:
    if len(urls) != len(dest_paths):
        raise ValueError("urls 与 dest_paths 长度不一致")

    results: List[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_download_one, url, dest): dest
            for url, dest in zip(urls, dest_paths)
        }
        for future in as_completed(future_map):
            dest = future_map[future]
            try:
                future.result()
                results.append(dest)
            except Exception:
                results.append(dest)
    return results


def _download_one(url: str, dest: str) -> None:
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0"}
    with requests.get(url, headers=headers, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
