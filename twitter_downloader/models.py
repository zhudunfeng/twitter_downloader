from __future__ import annotations


class MediaType:
    IMAGE = "image"
    VIDEO = "video"
    ARTICLE = "article"


class MediaItem:
    __slots__ = ("url", "media_type", "width", "height", "filename", "size_bytes")

    def __init__(
        self,
        url: str,
        media_type: str,
        *,
        width: int | None = None,
        height: int | None = None,
        filename: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        self.url = url
        self.media_type = media_type
        self.width = width
        self.height = height
        self.filename = filename
        self.size_bytes = size_bytes


class TweetInfo:
    __slots__ = (
        "tweet_id",
        "url",
        "author",
        "created_at",
        "text",
        "article_blocks",
        "article_urls",
        "media",
        "like_count",
        "retweet_count",
        "reply_count",
        "raw_info",
    )

    def __init__(
        self,
        tweet_id: str,
        url: str,
        author: "User",
        created_at=None,
        text: str = "",
        article_blocks: list["ArticleBlock"] | None = None,
        article_urls: list[str] | None = None,
        media: list[MediaItem] | None = None,
        like_count: int | None = None,
        retweet_count: int | None = None,
        reply_count: int | None = None,
        raw_info=None,
    ) -> None:
        self.tweet_id = tweet_id
        self.url = url
        self.author = author
        self.created_at = created_at
        self.text = text
        self.article_blocks = article_blocks or []
        self.article_urls = article_urls or []
        self.media = media or []
        self.like_count = like_count
        self.retweet_count = retweet_count
        self.reply_count = reply_count
        self.raw_info = raw_info

    @property
    def date_key(self) -> str:
        if hasattr(self.created_at, "strftime"):
            return self.created_at.strftime("%Y%m%d")
        return "00000000"


class ArticleBlock:
    __slots__ = ("index", "text")

    def __init__(self, index: int, text: str) -> None:
        self.index = index
        self.text = text


class User:
    __slots__ = ("username", "display_name", "user_id")

    def __init__(
        self,
        username: str,
        display_name: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.username = username
        self.display_name = display_name
        self.user_id = user_id
