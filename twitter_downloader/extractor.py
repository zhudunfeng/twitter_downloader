from __future__ import annotations

import datetime as dt
import re
from typing import List, Optional

import yt_dlp

from .models import ArticleBlock, MediaItem, TweetInfo, User


class BaseTweetExtractor:
    def extract(self, url: str) -> TweetInfo:
        raise NotImplementedError


class YtDlpTweetExtractor(BaseTweetExtractor):
    STATUS_TOKEN_RE = re.compile(r"/status/(\d+)")

    def extract(self, url: str) -> TweetInfo:
        match = self.STATUS_TOKEN_RE.search(url)
        tweet_id = match.group(1) if match else url
        ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True})
        info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            raise RuntimeError(f"未能解析推文信息: {url}")
        author = self._author(info)
        created_at = self._created_at(info)
        text = info.get("description") or info.get("title") or ""
        article_blocks: List[ArticleBlock] = []
        article_urls = []
        media = self._media(info)
        return TweetInfo(
            tweet_id=tweet_id,
            url=url,
            author=author,
            created_at=created_at,
            text=text,
            article_blocks=article_blocks,
            article_urls=article_urls,
            media=media,
            like_count=info.get("like_count"),
            retweet_count=info.get("repost_count"),
            reply_count=info.get("comment_count"),
            raw_info=info,
        )

    def _author(self, info: dict) -> User:
        uploader = info.get("uploader")
        if isinstance(uploader, dict):
            return User(
                username=uploader.get("uploader_id") or uploader.get("uploader") or "",
                display_name=uploader.get("uploader") or uploader.get("uploader_id"),
                user_id=uploader.get("channel_id"),
            )
        if uploader:
            return User(username=uploader, display_name=uploader)
        return User(username=info.get("uploader_id") or "", display_name=info.get("title"))

    def _created_at(self, info: dict) -> Optional[dt.datetime]:
        value = info.get("timestamp")
        if isinstance(value, int):
            try:
                return dt.datetime.fromtimestamp(
                    value, tz=dt.timezone.utc
                ).replace(tzinfo=None)
            except Exception:
                return None
        if isinstance(value, str):
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    return dt.datetime.strptime(value, fmt)
                except ValueError:
                    continue
        return None

    def _media(self, info: dict) -> List[MediaItem]:
        items: List[MediaItem] = []
        url = info.get("url")
        if url:
            items.append(MediaItem(url=url, media_type="video"))
        return items
