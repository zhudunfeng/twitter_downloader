from __future__ import annotations

import logging
import unittest
from os import unlink
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch

from click.testing import CliRunner

import twitter_downloader.logging_setup as logging_setup_module
from twitter_downloader.cli import MediaDownloader, app
from twitter_downloader.core import download_one
from twitter_downloader.downloader import Downloader
from twitter_downloader.extractor import YtDlpTweetExtractor
from twitter_downloader.logging_setup import get_logger, setup_logging
from twitter_downloader.models import MediaItem, TweetInfo, User
from twitter_downloader.output import OutputPathBuilder
from twitter_downloader.utils import is_tweet_url


class FixtureExtractor(YtDlpTweetExtractor):
    def __init__(self, status: dict) -> None:
        super().__init__()
        self.status = status

    def _extract_status(self, tweet_id: str) -> dict:
        self.requested_tweet_id = tweet_id
        return self.status


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def on_start(self, url: str) -> None:
        self.events.append(("start", url))

    def on_progress(self, percent: float, status: str) -> None:
        self.events.append(("progress", f"{percent}:{status}"))

    def on_log(self, message: str) -> None:
        self.events.append(("log", message))

    def on_done(self, path: str) -> None:
        self.events.append(("done", path))

    def on_error(self, url: str, error: str) -> None:
        self.events.append(("error", f"{url}:{error}"))


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

    def test_video_uses_best_mp4_variant_when_available(self) -> None:
        extractor = FixtureExtractor(
            {
                "id_str": "123",
                "full_text": "video post",
                "created_at": "2026-08-11T06:31:02.000Z",
                "user": {"screen_name": "alice", "name": "Alice", "id_str": "42"},
                "extended_entities": {
                    "media": [
                        {
                            "type": "video",
                            "original_info": {"width": 544, "height": 928},
                            "video_info": {
                                "variants": [
                                    {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/pl.m3u8"},
                                    {"content_type": "video/mp4", "bitrate": 632000, "url": "https://video.twimg.com/low.mp4"},
                                    {"content_type": "video/mp4", "bitrate": 2176000, "url": "https://video.twimg.com/high.mp4"},
                                ]
                            },
                        }
                    ]
                },
            }
        )

        tweet = extractor.extract("https://x.com/alice/status/123")

        self.assertEqual([item.url for item in tweet.media], ["https://video.twimg.com/high.mp4"])

    def test_tombstone_response_reports_authentication_requirement(self) -> None:
        extractor = FixtureExtractor(
            {
                "__typename": "TweetTombstone",
                "extended_entities": {"media": []},
            }
        )
        extractor.auto_cookies = False

        with self.assertRaisesRegex(RuntimeError, "authenticated session"):
            extractor.extract("https://x.com/alice/status/123")

    def test_empty_status_is_not_reported_as_a_successful_download(self) -> None:
        extractor = FixtureExtractor({"user": {}, "extended_entities": {"media": []}})
        extractor.auto_cookies = False

        with self.assertRaisesRegex(RuntimeError, "age-restricted"):
            extractor.extract("https://x.com/alice/status/123")

    def test_auto_cookie_retry_uses_detected_source(self) -> None:
        restricted = {"__typename": "TweetTombstone", "extended_entities": {"media": []}}
        allowed = {
            "id_str": "123",
            "full_text": "unlocked",
            "user": {"screen_name": "alice", "name": "Alice", "id_str": "42"},
            "extended_entities": {"media": []},
        }
        extractor = FixtureExtractor(restricted)
        extractor.status = None

        with patch("twitter_downloader.extractor.cookie_sources", return_value=iter(["browser=chrome"])) as detect:
            calls = []

            def fake_status(tweet_id: str) -> dict:
                calls.append(tweet_id)
                return restricted if len(calls) == 1 else allowed

            extractor._extract_status = fake_status
            tweet = extractor.extract("https://x.com/alice/status/123")

        detect.assert_called_once()
        self.assertEqual(extractor.active_cookies, "browser=chrome")
        self.assertEqual(tweet.text, "unlocked")

    def test_anonymous_mode_uses_syndication_endpoint(self) -> None:
        extractor = YtDlpTweetExtractor(use_authenticated=False)
        captured: dict = {}

        class FakeYdl:
            def __init__(self, options: dict) -> None:
                captured.update(options)

            def __enter__(self) -> "FakeYdl":
                return self

            def __exit__(self, *_exc) -> None:
                return None

            def get_info_extractor(self, _name: str):
                raise AssertionError("extractor should not be queried by this test")

        with patch("twitter_downloader.extractor.yt_dlp.YoutubeDL", FakeYdl):
            with self.assertRaises(AssertionError):
                extractor._extract_status("123")

        self.assertEqual(captured["extractor_args"], {"twitter": {"api": ["syndication"]}})

    def test_authenticated_mode_does_not_force_syndication(self) -> None:
        extractor = YtDlpTweetExtractor()
        captured: dict = {}

        class FakeYdl:
            def __init__(self, options: dict) -> None:
                captured.update(options)

            def __enter__(self) -> "FakeYdl":
                return self

            def __exit__(self, *_exc) -> None:
                return None

            def get_info_extractor(self, _name: str):
                raise AssertionError("stop here")

        with patch("twitter_downloader.extractor.yt_dlp.YoutubeDL", FakeYdl):
            with self.assertRaises(AssertionError):
                extractor._extract_status("123")

        self.assertNotIn("extractor_args", captured)

    def test_anonymous_mode_does_not_retry_with_cookies(self) -> None:
        extractor = FixtureExtractor({"__typename": "TweetTombstone", "extended_entities": {"media": []}})
        extractor.auto_cookies = True
        extractor.use_authenticated = False

        with patch("twitter_downloader.extractor.cookie_sources") as sources:
            with self.assertRaisesRegex(RuntimeError, "authenticated API option"):
                extractor.extract("https://x.com/alice/status/123")

        sources.assert_not_called()

    def test_model_accepts_article_urls(self) -> None:
        tweet = TweetInfo("1", "https://x.com/alice/status/1", User("alice"), article_urls=["https://example.com/article"])
        self.assertEqual(tweet.article_urls, ["https://example.com/article"])

    def test_url_validation_accepts_query_fragment_and_media_path(self) -> None:
        self.assertTrue(is_tweet_url("https://x.com/alice/status/123?s=20"))
        self.assertTrue(is_tweet_url("https://twitter.com/alice/status/123/photo/1#details"))

    def test_downloader_enforces_the_eight_worker_limit(self) -> None:
        with self.assertRaises(ValueError):
            Downloader(max_workers=9)

    def test_metadata_omits_article_section_when_no_article_exists(self) -> None:
        tweet = TweetInfo("123", "https://x.com/alice/status/123", User("alice"))
        with TemporaryDirectory() as output_dir:
            destination = Downloader(OutputPathBuilder(output_dir)).download(tweet)
            with open(f"{destination}/tweet.md", encoding="utf-8") as handle:
                markdown = handle.read()

        self.assertNotIn("## article", markdown)

    def test_video_download_forwards_yt_dlp_logs_to_progress_handler(self) -> None:
        progress = RecordingProgress()
        downloader = Downloader(progress=progress, verbose=True)
        video = MediaItem("video", "https://x.com/alice/status/123/video/1")

        with patch("twitter_downloader.downloader.yt_dlp.YoutubeDL") as youtube_dl, patch(
            "twitter_downloader.downloader.os.path.isfile", return_value=True
        ):
            youtube_dl.return_value.__enter__.return_value.download.return_value = 0
            downloader._save_video(video, "downloads", "123", 1)

        options = youtube_dl.call_args.args[0]
        options["logger"].debug("extracting media")
        options["logger"].warning("rate limited")
        self.assertIn(("log", "extracting media"), progress.events)
        self.assertIn(("log", "WARNING: rate limited"), progress.events)

    def test_core_reports_start_progress_and_completion(self) -> None:
        url = "https://x.com/alice/status/123"
        tweet = TweetInfo("123", url, User("alice"))
        progress = RecordingProgress()

        class FakeDownloader:
            def __init__(self, **kwargs) -> None:
                self.progress = kwargs["progress"]

            def download(self, downloaded_tweet: TweetInfo) -> str:
                self.progress.on_progress(50.0, "downloading")
                self.progress.on_done("downloads/alice/00000000/123")
                return "downloads/alice/00000000/123"

        with patch("twitter_downloader.core.YtDlpTweetExtractor") as extractor, patch(
            "twitter_downloader.core.Downloader", FakeDownloader
        ):
            extractor.return_value.extract.return_value = tweet
            destination = download_one(url, progress=progress)

        self.assertEqual(destination, "downloads/alice/00000000/123")
        self.assertEqual(
            progress.events,
            [
                ("start", url),
                ("progress", "50.0:downloading"),
                ("done", "downloads/alice/00000000/123"),
            ],
        )

    def test_core_reports_errors_before_reraising(self) -> None:
        url = "https://x.com/alice/status/123"
        progress = RecordingProgress()

        with patch("twitter_downloader.core.YtDlpTweetExtractor") as extractor:
            extractor.return_value.extract.side_effect = RuntimeError("private post")
            with self.assertRaisesRegex(RuntimeError, "private post"):
                download_one(url, progress=progress)

        self.assertEqual(progress.events, [("start", url), ("error", f"{url}:private post")])

    def test_core_reports_invalid_urls_to_progress_handler(self) -> None:
        url = "https://example.com/not-a-tweet"
        progress = RecordingProgress()

        with self.assertRaisesRegex(ValueError, "Invalid Twitter/X URL"):
            download_one(url, progress=progress)

        self.assertEqual(progress.events, [("start", url), ("error", f"{url}:Invalid Twitter/X URL: {url}")])

    def test_cli_delegates_downloads_to_the_core_flow(self) -> None:
        client = MediaDownloader()
        url = "https://x.com/alice/status/123"
        with patch("twitter_downloader.cli.download_one", return_value="downloads/alice/20260811/123") as download:
            destination = client.download(url, "downloads", cookies="cookies.txt", verbose=True)

        self.assertEqual(destination, "downloads/alice/20260811/123")
        download.assert_called_once_with(
            url,
            "downloads",
            cookies="cookies.txt",
            verbose=True,
            use_authenticated=True,
        )

    def test_batch_skips_comments_and_duplicates_and_collects_failures(self) -> None:
        good_url = "https://x.com/alice/status/123"
        failing_url = "https://x.com/bob/status/456"
        client = MediaDownloader()

        with patch.object(
            client,
            "download",
            side_effect=["downloads/alice/20260811/123", RuntimeError("private post")],
        ) as download:
            result = client.batch(["# source", "", good_url, good_url, failing_url])

        self.assertEqual(result.destinations, ["downloads/alice/20260811/123"])
        self.assertEqual([(failure.url, failure.error) for failure in result.failures], [(failing_url, "private post")])
        self.assertEqual(download.call_count, 2)

    def test_batch_command_returns_nonzero_when_any_download_fails(self) -> None:
        url = "https://x.com/alice/status/123"
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            handle.write(f"# a comment\n{url}\n")
            input_path = handle.name
        self.addCleanup(unlink, input_path)

        with patch.object(MediaDownloader, "download", side_effect=RuntimeError("private post")):
            result = CliRunner().invoke(app, ["batch", input_path])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("完成：成功 0 条，失败 1 条。", result.output)
        self.assertIn("private post", result.output)


class LoggingTests(unittest.TestCase):
    """验证日志同时输出到控制台与文件。"""

    def setUp(self) -> None:
        # 每个用例前重置，避免上一个用例的 handler 占用文件句柄
        _reset_logging()

    def tearDown(self) -> None:
        # 用例结束后立即关闭 handler，否则 Windows 下临时目录无法删除
        _reset_logging()

    def test_setup_logging_writes_to_console_and_file(self) -> None:
        with TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "app.log"
            path = setup_logging(verbose=True, log_path=log_file)

            get_logger("test").info("双路日志测试")
            _reset_logging()

            self.assertEqual(path, log_file)
            self.assertTrue(log_file.is_file())
            content = log_file.read_text(encoding="utf-8")
        self.assertIn("双路日志测试", content)

    def test_repeated_setup_does_not_duplicate_handlers(self) -> None:
        with TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "app.log"
            setup_logging(verbose=False, log_path=log_file)
            setup_logging(verbose=True, log_path=log_file)
            get_logger("test").info("只应出现一次")
            _reset_logging()

            content = log_file.read_text(encoding="utf-8")

        self.assertEqual(content.count("只应出现一次"), 1)

    def test_file_handler_keeps_debug_logs_when_not_verbose(self) -> None:
        with TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "app.log"
            setup_logging(verbose=False, log_path=log_file)

            get_logger("test").debug("调试细节")
            get_logger("test").info("普通信息")
            _reset_logging()

            content = log_file.read_text(encoding="utf-8")

        # 控制台只显示 INFO，但文件应保留 DEBUG
        self.assertIn("调试细节", content)
        self.assertIn("普通信息", content)


def _reset_logging() -> None:
    """清空项目 logger 的 handler，便于测试重复初始化。"""
    root = logging.getLogger("twitter_downloader")
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    logging_setup_module._configured = False
    logging_setup_module._console_handler = None


if __name__ == "__main__":
    unittest.main()
