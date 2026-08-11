from __future__ import annotations

import io
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

import requests
from PIL import Image

_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:twitter\.com|x\.com)/[^/]+/status/\d+(?:\?.*)?$"
)


def sanitize_filename(name: str) -> str:
    value = unicodedata.normalize("NFKD", name)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\-. ]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    return value.strip("_") or "output"


def is_tweet_url(value: str) -> bool:
    return bool(_URL_RE.match(value.strip()))


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
