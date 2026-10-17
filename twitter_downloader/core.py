from __future__ import annotations

from typing import Iterable

from .downloader import Downloader
from .extractor import YtDlpTweetExtractor
from .logging_setup import get_logger
from .output import OutputPathBuilder
from .progress import ProgressHandler
from .utils import ensure_dir, is_tweet_url

# 下载流程统一日志器，输出会同时到达控制台与日志文件
_logger = get_logger("core")


def download_one(
    url: str,
    output_dir: str = "downloads",
    *,
    cookies: str | None = None,
    verbose: bool = False,
    use_authenticated: bool = True,
    progress: ProgressHandler | None = None,
) -> str:
    """下载单条推文，返回落盘目录；失败时抛出异常。"""
    if progress is not None:
        progress.on_start(url)
    _logger.info("开始下载: %s", url)
    try:
        if not is_tweet_url(url):
            raise ValueError(f"Invalid Twitter/X URL: {url}")
        ensure_dir(output_dir)
        # 认证/匿名接口由 use_authenticated 控制，默认走认证路径
        extractor = YtDlpTweetExtractor(cookies=cookies, use_authenticated=use_authenticated)
        tweet = extractor.extract(url)
        _logger.info(
            "解析完成: @%s, 媒体 %d 个, 使用 cookie 来源 %s",
            tweet.author.username,
            len(tweet.media),
            extractor.active_cookies or "无",
        )
        destination = Downloader(
            output_path_builder=OutputPathBuilder(base_dir=output_dir),
            cookies=extractor.active_cookies,
            verbose=verbose,
            progress=progress,
        ).download(tweet)
        _logger.info("下载完成: %s", destination)
        return destination
    except Exception as exc:
        _logger.error("下载失败 %s: %s", url, exc)
        if progress is not None:
            progress.on_error(url, str(exc))
        raise


def download_batch(
    urls: Iterable[str],
    output_dir: str = "downloads",
    *,
    cookies: str | None = None,
    use_authenticated: bool = True,
    progress: ProgressHandler | None = None,
) -> list[str]:
    results: list[str] = []
    for url in urls:
        results.append(
            download_one(
                url,
                output_dir,
                cookies=cookies,
                use_authenticated=use_authenticated,
                progress=progress,
            )
        )
    return results
