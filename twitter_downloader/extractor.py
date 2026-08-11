from __future__ import annotations

import datetime as dt
import re
from typing import Any, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yt_dlp

from .models import ArticleBlock, MediaItem, TweetInfo, User


class BaseTweetExtractor:
    def extract(self, url: str) -> TweetInfo:
        raise NotImplementedError


class YtDlpTweetExtractor(BaseTweetExtractor):
    STATUS_TOKEN_RE = re.compile(r"/status/(\d+)")

    def __init__(self, cookies: str | None = None) -> None:
        self.cookies = cookies

    def extract(self, url: str) -> TweetInfo:
        match = self.STATUS_TOKEN_RE.search(url)
        if not match:
            raise RuntimeError(f"Unable to determine tweet id from URL: {url}")
        tweet_id = match.group(1)
        info = self._extract_status(tweet_id)
        author = self._author(info)
        created_at = self._created_at(info)
        text = info.get("full_text") or info.get("text") or info.get("description") or ""
        return TweetInfo(
            tweet_id=tweet_id,
            url=url,
            author=author,
            created_at=created_at,
            text=text,
            article_blocks=self._article_blocks(info),
            media=self._media(info, url),
            like_count=info.get("favorite_count") or info.get("like_count"),
            retweet_count=info.get("retweet_count") or info.get("repost_count"),
            reply_count=info.get("reply_count") or info.get("comment_count"),
            raw_info=info,
        )

    def _extract_status(self, tweet_id: str) -> dict[str, Any]:
        """Read the full tweet state through yt-dlp's Twitter integration."""
        options: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True}
        self._apply_cookie_options(options)
        with yt_dlp.YoutubeDL(options) as ydl:
            extractor = ydl.get_info_extractor("Twitter")
            fetch_status = getattr(extractor, "_call_syndication_api", None)
            if not callable(fetch_status):
                raise RuntimeError("Installed yt-dlp does not support Twitter status extraction")
            status = fetch_status(tweet_id)
        if not isinstance(status, dict):
            raise RuntimeError(f"Unable to extract tweet information: {tweet_id}")
        return status

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

    @staticmethod
    def _author(info: dict[str, Any]) -> User:
        user = info.get("user") or {}
        if isinstance(user, dict):
            return User(
                username=user.get("screen_name") or info.get("uploader_id") or "unknown",
                display_name=user.get("name") or info.get("uploader"),
                user_id=user.get("id_str") or info.get("channel_id"),
            )
        return User(
            username=info.get("uploader_id") or "unknown",
            display_name=info.get("uploader"),
            user_id=info.get("channel_id"),
        )

    @staticmethod
    def _created_at(info: dict[str, Any]) -> Optional[dt.datetime]:
        value = info.get("timestamp") or info.get("created_at")
        if isinstance(value, int):
            return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).replace(tzinfo=None)
        if isinstance(value, str):
            for fmt in (
                "%a %b %d %H:%M:%S %z %Y",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    return dt.datetime.strptime(value, fmt).replace(tzinfo=None)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _article_blocks(info: dict[str, Any]) -> List[ArticleBlock]:
        article = info.get("article") or {}
        if not isinstance(article, dict):
            return []
        text = article.get("plain_text") or article.get("text")
        return [ArticleBlock(1, text)] if isinstance(text, str) and text else []

    def _media(self, info: dict[str, Any], tweet_url: str) -> List[MediaItem]:
        entities = info.get("extended_entities") or {}
        entries = entities.get("media") if isinstance(entities, dict) else []
        items: List[MediaItem] = []
        for media_index, entry in enumerate(entries or [], start=1):
            if not isinstance(entry, dict):
                continue
            media_type = entry.get("type")
            width, height = self._dimensions(entry)
            if media_type == "photo":
                image_url = self._original_image_url(entry)
                if image_url:
                    items.append(MediaItem(image_url, "image", width=width, height=height))
            elif media_type in {"video", "animated_gif"}:
                items.append(
                    MediaItem(
                        self._video_url(tweet_url, media_index),
                        "video",
                        width=width,
                        height=height,
                    )
                )
        return items

    @staticmethod
    def _dimensions(entry: dict[str, Any]) -> tuple[int | None, int | None]:
        original = entry.get("original_info") or {}
        if not isinstance(original, dict):
            return None, None
        return original.get("width") or original.get("w"), original.get("height") or original.get("h")

    @staticmethod
    def _original_image_url(entry: dict[str, Any]) -> str | None:
        media_url = entry.get("media_url_https") or entry.get("media_url")
        if not isinstance(media_url, str) or not media_url:
            return None
        parts = urlsplit(media_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["name"] = "orig"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def _video_url(tweet_url: str, index: int) -> str:
        parts = urlsplit(tweet_url)
        path = re.sub(r"/(?:video|photo)/\d+$", "", parts.path)
        return urlunsplit((parts.scheme, parts.netloc, f"{path}/video/{index}", parts.query, ""))
