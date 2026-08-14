from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from glob import glob
from typing import Any, Callable, cast

import yt_dlp

from .models import MediaItem, TweetInfo
from .output import OutputPathBuilder
from .progress import ProgressHandler


class _ProgressLogger:
    def __init__(self, progress: ProgressHandler, verbose: bool) -> None:
        self.progress = progress
        self.verbose = verbose

    def debug(self, message: str) -> None:
        if self.verbose:
            self.progress.on_log(message)

    def info(self, message: str) -> None:
        if self.verbose:
            self.progress.on_log(message)

    def warning(self, message: str) -> None:
        self.progress.on_log(f"WARNING: {message}")

    def error(self, message: str) -> None:
        self.progress.on_log(f"ERROR: {message}")


class Downloader:
    def __init__(
        self,
        output_path_builder: OutputPathBuilder | None = None,
        *,
        max_workers: int = 8,
        cookies: str | None = None,
        verbose: bool = False,
        progress: ProgressHandler | None = None,
    ) -> None:
        if not 1 <= max_workers <= 8:
            raise ValueError("max_workers must be between 1 and 8")
        self.output_path_builder = output_path_builder or OutputPathBuilder()
        self.max_workers = max_workers
        self.cookies = cookies
        self.verbose = verbose
        self.progress = progress

    def download(self, tweet: TweetInfo) -> str:
        target_dir = self.output_path_builder.tweet_dir(
            tweet.author.username,
            tweet.created_at,
            tweet.tweet_id,
        )
        os.makedirs(target_dir, exist_ok=True)
        videos = [item for item in tweet.media if item.media_type == "video"]
        images = [item for item in tweet.media if item.media_type == "image"]
        for index, item in enumerate(videos, start=1):
            self._save_video(item, target_dir, tweet.tweet_id, index)
        if images:
            self._save_images(images, target_dir, tweet.tweet_id)
        self._save_metadata(tweet, target_dir)
        if self.progress is not None:
            self.progress.on_done(target_dir)
        return target_dir

    def _save_video(self, item: MediaItem, target_dir: str, tweet_id: str, index: int) -> None:
        stem = f"{tweet_id}_video_{index}"
        output_template = os.path.join(target_dir, f"{stem}.%(ext)s")
        options: dict[str, Any] = {
            "continuedl": True,
            "ignoreerrors": False,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "outtmpl": output_template,
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
            "quiet": not self.verbose,
            "restrictfilenames": True,
            "verbose": self.verbose,
            "format": "bestvideo+bestaudio/best",
            "max_filesize": 1024 ** 3,
            "socket_timeout": 30,
            "retries": 3,
        }
        self._apply_cookie_options(options)
        if self.progress is not None:
            options["progress_hooks"] = [self._build_progress_hook()]
            options["logger"] = _ProgressLogger(self.progress, self.verbose)
        with yt_dlp.YoutubeDL(cast(dict, options)) as ydl:
            result = ydl.download([item.url])
        expected_path = os.path.join(target_dir, f"{stem}.mp4")
        if result or not os.path.isfile(expected_path):
            files = ", ".join(os.path.basename(path) for path in glob(os.path.join(target_dir, f"{stem}.*")))
            raise RuntimeError(f"Video download did not produce an MP4 file ({files or 'no output'})")

    def _build_progress_hook(self) -> Callable[[dict[str, Any]], None]:
        progress = self.progress

        def hook(download: dict[str, Any]) -> None:
            if progress is None:
                return
            status = download.get("status") or ""
            if status == "downloading":
                total = download.get("total_bytes") or download.get("total_bytes_estimate") or download.get("filesize")
                downloaded = download.get("downloaded_bytes")
                if isinstance(total, (int, float)) and total > 0 and isinstance(downloaded, (int, float)):
                    percent = max(0.0, min(float(downloaded) / float(total), 1.0)) * 100.0
                    progress.on_progress(percent, "downloading")
            elif status == "finished":
                progress.on_progress(100.0, "finished")

        return hook

    def _apply_cookie_options(self, options: dict[str, Any]) -> None:
        if not self.cookies:
            return
        if self.cookies.startswith("browser="):
            browser = self.cookies.partition("=")[2].strip()
            if not browser:
                raise ValueError("browser cookie source cannot be empty")
            options["cookiesfrombrowser"] = (browser,)
        else:
            options["cookiefile"] = self.cookies

    def _save_images(self, items: list[MediaItem], target_dir: str, tweet_id: str) -> None:
        from .utils import download_image_to_jpg

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {}
            for index, item in enumerate(items, start=1):
                path = os.path.join(target_dir, f"{tweet_id}_{index}.jpg")
                futures[pool.submit(download_image_to_jpg, item.url, path)] = path
            for future in as_completed(futures):
                path = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    raise RuntimeError(f"Image download failed {path}: {exc}") from exc

    def _save_metadata(self, tweet: TweetInfo, target_dir: str) -> None:
        payload = {
            "tweet_id": tweet.tweet_id,
            "url": tweet.url,
            "author": {
                "username": tweet.author.username,
                "display_name": tweet.author.display_name,
                "user_id": tweet.author.user_id,
            },
            "created_at": tweet.created_at.isoformat() if tweet.created_at else None,
            "text": tweet.text,
            "article_blocks": [{"index": block.index, "text": block.text} for block in tweet.article_blocks],
            "article_urls": tweet.article_urls,
            "media": [
                {"type": item.media_type, "url": item.url, "width": item.width, "height": item.height}
                for item in tweet.media
            ],
            "like_count": tweet.like_count,
            "retweet_count": tweet.retweet_count,
            "reply_count": tweet.reply_count,
        }
        with open(os.path.join(target_dir, "tweet.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        lines = [
            f"# tweet {tweet.tweet_id}",
            "",
            f"- author: @{tweet.author.username} ({tweet.author.display_name or ''})",
            f"- date: {tweet.created_at.isoformat() if tweet.created_at else 'unknown'}",
            f"- url: {tweet.url}",
            "",
        ]
        if tweet.text:
            lines.extend(["## text", "", tweet.text, ""])
        if tweet.article_blocks:
            lines.extend(["## article", ""])
            lines.extend(f"{block.index}. {block.text}" for block in tweet.article_blocks)
            lines.append("")
        with open(os.path.join(target_dir, "tweet.md"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
