from __future__ import annotations

import unittest
from os import unlink
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch

from click.testing import CliRunner

from twitter_downloader.cli import MediaDownloader, app
from twitter_downloader.core import download_one
from twitter_downloader.downloader import Downloader
from twitter_downloader.extractor import YtDlpTweetExtractor
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
        download.assert_called_once_with(url, "downloads", cookies="cookies.txt", verbose=True)

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
        self.assertIn("Completed: 0 succeeded, 1 failed.", result.output)
        self.assertIn("private post", result.output)


if __name__ == "__main__":
    unittest.main()
