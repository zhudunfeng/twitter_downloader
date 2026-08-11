from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import cast

import yt_dlp

from .models import MediaItem, TweetInfo
from .output import OutputPathBuilder
from .utils import ensure_dir


class Downloader:
    def __init__(self, output_path_builder: OutputPathBuilder | None = None, *, max_workers: int = 8) -> None:
        self.output_path_builder = output_path_builder or OutputPathBuilder()
        self.max_workers = max_workers

    def download(self, tweet: TweetInfo) -> str:
        target_dir = self.output_path_builder.tweet_dir(
            tweet.author.username,
            tweet.created_at,
            tweet.tweet_id,
        )
        os.makedirs(target_dir, exist_ok=True)
        video_items = [item for item in tweet.media if item.media_type == "video"]
        image_items = [item for item in tweet.media if item.media_type == "image"]
        for url in tweet.article_urls:
            if url:
                image_items.append(MediaItem(url=url, media_type="image"))
        if video_items:
            self._save_video(video_items[0], target_dir, tweet.tweet_id)
        if image_items:
            self._save_images(image_items, target_dir, tweet.tweet_id)

        self._save_metadata(tweet, target_dir)
        return target_dir

    def _save_video(self, item: MediaItem, target_dir: str, tweet_id: str) -> None:
        outtmpl = os.path.join(target_dir, f"{tweet_id}.%(ext)s")
        ydl_opts: dict = {
            "contsort": "res,fps",
            "continuedl": True,
            "cookiefile": None,
            "ignoreerrors": True,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "outtmpl": outtmpl,
            "postprocessors": [
                {
                    "key": "FFmpegMerger",
                    "nopostoverwrites": False,
                }
            ],
            "quiet": False,
            "restrictfilenames": True,
            "verbose": True,
            "format": "bestvideo+bestaudio/best",
            "max_filesize": "1G",
            "socket_timeout": 30,
            "retries": 3,
        }
        with yt_dlp.YoutubeDL(cast(dict, ydl_opts)) as ydl:
            ydl.download([item.url])

    def _save_images(self, items: list[MediaItem], target_dir: str, tweet_id: str) -> None:
        from .utils import download_image_to_jpg

        jobs: list[tuple[str, str, str]] = []
        for idx, item in enumerate(items, start=1):
            filename = f"{tweet_id}_{idx}.jpg"
            dest = os.path.join(target_dir, filename)
            jobs.append((item.url, dest, filename))
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(download_image_to_jpg, url, dest): dest
                for url, dest, _ in jobs
            }
            for future in as_completed(futures):
                path = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    raise RuntimeError(f"图片下载失败 {path}: {exc}") from exc

    def _save_metadata(self, tweet: TweetInfo, target_dir: str) -> None:
        payload = {
            "tweet_id": tweet.tweet_id,
            "url": tweet.url,
            "author": {
                "username": tweet.author.username,
                "display_name": tweet.author.display_name,
            },
            "created_at": tweet.created_at.isoformat() if tweet.created_at else None,
            "text": tweet.text,
            "article_blocks": [
                {"index": block.index, "text": block.text}
                for block in tweet.article_blocks
            ],
            "like_count": tweet.like_count,
            "retweet_count": tweet.retweet_count,
            "reply_count": tweet.reply_count,
        }
        with open(os.path.join(target_dir, "tweet.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        lines = (
            [f"# tweet {tweet.tweet_id}", ""]
            + [
                f"- author: @{tweet.author.username} ({tweet.author.display_name or ''})",
                f"- date: {tweet.created_at.isoformat() if tweet.created_at else 'unknown'}",
                f"- url: {tweet.url}",
                "",
            ]
            + (["## text", "", tweet.text, ""] if tweet.text else [])
        )
        if tweet.article_blocks:
            lines.extend(["## article", ""])
            for idx, block in enumerate(tweet.article_blocks, start=1):
                lines.append(f"{idx}. {block.text}")
            lines.append("")
        with open(os.path.join(target_dir, "tweet.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
