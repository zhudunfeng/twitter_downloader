from __future__ import annotations

from typing import Iterable

from .downloader import Downloader
from .extractor import YtDlpTweetExtractor
from .output import OutputPathBuilder
from .progress import ProgressHandler
from .utils import ensure_dir, is_tweet_url


def download_one(
    url: str,
    output_dir: str = "downloads",
    *,
    cookies: str | None = None,
    verbose: bool = False,
    progress: ProgressHandler | None = None,
) -> str:
    if progress is not None:
        progress.on_start(url)
    try:
        if not is_tweet_url(url):
            raise ValueError(f"Invalid Twitter/X URL: {url}")
        ensure_dir(output_dir)
        tweet = YtDlpTweetExtractor(cookies=cookies).extract(url)
        return Downloader(
            output_path_builder=OutputPathBuilder(base_dir=output_dir),
            cookies=cookies,
            verbose=verbose,
            progress=progress,
        ).download(tweet)
    except Exception as exc:
        if progress is not None:
            progress.on_error(url, str(exc))
        raise


def download_batch(
    urls: Iterable[str],
    output_dir: str = "downloads",
    *,
    cookies: str | None = None,
    progress: ProgressHandler | None = None,
) -> list[str]:
    results: list[str] = []
    for url in urls:
        results.append(download_one(url, output_dir, cookies=cookies, progress=progress))
    return results
