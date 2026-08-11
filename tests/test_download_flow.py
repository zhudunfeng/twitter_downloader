from __future__ import annotations

import unittest

from twitter_downloader.downloader import Downloader
from twitter_downloader.extractor import YtDlpTweetExtractor
from twitter_downloader.models import TweetInfo, User
from twitter_downloader.utils import is_tweet_url


class FixtureExtractor(YtDlpTweetExtractor):
    def __init__(self, status: dict) -> None:
        super().__init__()
        self.status = status

    def _extract_status(self, tweet_id: str) -> dict:
        self.requested_tweet_id = tweet_id
        return self.status


class DownloadFlowTests(unittest.TestCase):
    def test_extracts_text_photo_and_video(self) -> None:
        extractor = FixtureExtractor(
            {
                "text": "A post with every media type",
                "created_at": "2026-08-11T06:31:02.000Z",
                "user": {"screen_name": "alice", "name": "Alice", "id_str": "42"},
                "extended_entities": {
                    "media": [
                        {
                            "type": "photo",
                            "media_url_https": "https://pbs.twimg.com/media/photo.jpg?name=small",
                            "original_info": {"width": 1200, "height": 800},
                        },
                        {"type": "video", "original_info": {"width": 1920, "height": 1080}},
                    ]
                },
            }
        )

        tweet = extractor.extract("https://x.com/alice/status/12345?s=20")

        self.assertEqual(extractor.requested_tweet_id, "12345")
        self.assertEqual(tweet.text, "A post with every media type")
        self.assertEqual(tweet.author.user_id, "42")
        self.assertEqual(tweet.date_key, "20260811")
        self.assertEqual([item.media_type for item in tweet.media], ["image", "video"])
        self.assertIn("name=orig", tweet.media[0].url)
        self.assertEqual(tweet.media[1].url, "https://x.com/alice/status/12345/video/2?s=20")

    def test_model_accepts_article_urls(self) -> None:
        tweet = TweetInfo("1", "https://x.com/alice/status/1", User("alice"), article_urls=["https://example.com/article"])
        self.assertEqual(tweet.article_urls, ["https://example.com/article"])

    def test_url_validation_accepts_query_fragment_and_media_path(self) -> None:
        self.assertTrue(is_tweet_url("https://x.com/alice/status/123?s=20"))
        self.assertTrue(is_tweet_url("https://twitter.com/alice/status/123/photo/1#details"))

    def test_downloader_enforces_the_eight_worker_limit(self) -> None:
        with self.assertRaises(ValueError):
            Downloader(max_workers=9)


if __name__ == "__main__":
    unittest.main()
