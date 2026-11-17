from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from glob import glob
from tempfile import NamedTemporaryFile
from typing import Any, cast

import yt_dlp

from .models import MediaItem, TweetInfo
from .output import OutputPathBuilder
from .progress import ProgressHandler

logger = logging.getLogger(__name__)
METADATA_SCHEMA_VERSION = 2  # 下载清单版本；新增本地文件名、媒体顺序和实际大小，保留所有旧字段。


class _ProgressLogger:
    """把第三方下载器消息转发给现有进度接口。"""

    def __init__(self, progress: ProgressHandler, verbose: bool) -> None:
        """保存进度接收方和是否输出调试日志的配置。"""
        self.progress = progress  # 下载进度和业务日志的接收方。
        self.verbose = verbose  # 是否输出 yt-dlp 的详细调试消息。

    def debug(self, message: str) -> None:
        """仅在详细日志模式下转发调试消息。"""
        if self.verbose:
            self.progress.on_log(message)

    def info(self, message: str) -> None:
        """仅在详细日志模式下转发常规消息。"""
        if self.verbose:
            self.progress.on_log(message)

    def warning(self, message: str) -> None:
        """转发警告并保留既有日志前缀约定。"""
        self.progress.on_log(f"WARNING: {message}")

    def error(self, message: str) -> None:
        """转发错误并保留既有日志前缀约定。"""
        self.progress.on_log(f"ERROR: {message}")


class Downloader:
    """把推文媒体和正文落盘，并发布可供离线作品库读取的快照。"""

    def __init__(
        self,
        output_path_builder: OutputPathBuilder | None = None,
        *,
        max_workers: int = 8,
        cookies: str | None = None,
        verbose: bool = False,
        progress: ProgressHandler | None = None,
    ) -> None:
        """配置输出目录构建器、最多八个图片线程、Cookie、调试与进度接口。"""
        if not 1 <= max_workers <= 8:
            raise ValueError("max_workers must be between 1 and 8")
        self.output_path_builder = output_path_builder or OutputPathBuilder()  # 生成账号/日期/推文目录的路径策略。
        self.max_workers = max_workers  # 单条推文的图片下载并发数，允许 1 至 8。
        self.cookies = cookies  # Cookie 文件路径或 browser=浏览器名称，空值表示匿名下载。
        self.verbose = verbose  # 是否开启 yt-dlp 调试日志。
        self.progress = progress  # 可选的下载进度和中文业务日志接收方。

    def download(self, tweet: TweetInfo) -> str:
        """下载全部媒体并原子发布元数据，完成后返回原有目录字符串。"""
        target_dir = self.output_path_builder.tweet_dir(
            tweet.author.username,
            tweet.created_at,
            tweet.tweet_id,
        )
        os.makedirs(target_dir, exist_ok=True)
        self._log(f"开始保存推文作品：{tweet.tweet_id}")
        videos = [item for item in tweet.media if item.media_type == "video"]
        images = [item for item in tweet.media if item.media_type == "image"]
        for index, item in enumerate(videos, start=1):
            self._save_video(item, target_dir, tweet.tweet_id, index)
        if images:
            self._save_images(images, target_dir, tweet.tweet_id)
        self._save_metadata(tweet, target_dir)
        self._log(f"作品已保存，正文和媒体清单可供离线查看：{target_dir}")
        if self.progress is not None:
            self.progress.on_done(target_dir)
        return target_dir

    def _log(self, message: str) -> None:
        """同时记录中文业务日志并通知已配置的界面或命令行进度接收方。"""
        logger.info(message)
        if self.progress is not None:
            self.progress.on_log(message)

    def _save_video(self, item: MediaItem, target_dir: str, tweet_id: str, index: int) -> None:
        """下载一个视频并记录最终 MP4 的本地文件名与实际字节数。"""
        stem = f"{tweet_id}_video_{index}"
        output_template = os.path.join(target_dir, f"{stem}.%(ext)s")
        options: dict[str, Any] = {
            "continuedl": True,  # 已存在的下载分片允许断点续传。
            "ignoreerrors": False,  # 媒体失败时阻止发布完成清单。
            "merge_output_format": "mp4",  # 合并音视频时使用 MP4 容器。
            "noplaylist": True,  # 每个链接仅下载指定媒体。
            "outtmpl": output_template,  # 保持既有推文 ID 和视频序号命名。
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],  # 以 MP4 容器重封装视频。
            "quiet": not self.verbose,  # 普通模式隐藏第三方冗长输出。
            "restrictfilenames": True,  # 使用安全的文件名字符。
            "verbose": self.verbose,  # 由用户控制 yt-dlp 调试输出。
            "format": "bestvideo+bestaudio/best",  # 优先选择最佳音视频并合并。
            "max_filesize": 1024 ** 3,  # 单视频最大文件大小为 1 GiB。
            "socket_timeout": 30,  # 网络套接字超时时间为 30 秒。
            "retries": 3,  # 网络失败最多重试三次。
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
        self._record_media(item, expected_path)
        self._log(f"视频已保存：{os.path.basename(expected_path)}")

    def _build_progress_hook(self) -> Callable[[dict[str, Any]], None]:
        """将 yt-dlp 的字节进度转换为现有百分比回调。"""
        progress = self.progress

        def hook(download: dict[str, Any]) -> None:
            """转发单次下载事件，缺少总字节数时等待后续事件。"""
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
        """按用户配置为第三方下载参数添加 Cookie 来源。"""
        if not self.cookies:
            return
        if self.cookies.startswith("browser="):
            browser = self.cookies.partition("=")[2].strip()
            if not browser:
                raise ValueError("browser cookie source cannot be empty")
            options["cookiesfrombrowser"] = (browser,)  # 从指定浏览器读取已授权的登录 Cookie。
        else:
            options["cookiefile"] = self.cookies  # 从指定 Netscape 格式文件读取 Cookie。

    def _save_images(self, items: list[MediaItem], target_dir: str, tweet_id: str) -> None:
        """最多使用八个工作线程保存 JPEG，并回填最终文件信息。"""
        from .utils import download_image_to_jpg

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {}
            for index, item in enumerate(items, start=1):
                path = os.path.join(target_dir, f"{tweet_id}_{index}.jpg")
                futures[pool.submit(download_image_to_jpg, item.url, path)] = (path, item)
            for future in as_completed(futures):
                path, item = futures[future]
                try:
                    future.result()
                    self._record_media(item, path)
                except Exception as exc:
                    raise RuntimeError(f"Image download failed {path}: {exc}") from exc
        self._log(f"图片已保存：共 {len(items)} 张")

    @staticmethod
    def _record_media(item: MediaItem, path: str) -> None:
        """记录由下载器生成的文件名；实际字节数未知时保持为空。"""
        item.filename = os.path.basename(path)
        try:
            item.size_bytes = os.path.getsize(path)
        except OSError:
            item.size_bytes = None

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        """在同目录写入完整临时文件后替换目标，异常时清理临时文件。"""
        temporary = ""
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=os.path.dirname(path),
                                    prefix=".tweet-", suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _save_metadata(self, tweet: TweetInfo, target_dir: str) -> None:
        """保持旧字段和媒体数组原序，最后原子发布带本地媒体清单的 JSON。"""
        media: list[dict[str, Any]] = []
        counters = {"image": 0, "video": 0}
        for order, item in enumerate(tweet.media):
            if item.media_type in counters:
                counters[item.media_type] += 1
                number = counters[item.media_type]
                filename = f"{tweet.tweet_id}_{number}.jpg" if item.media_type == "image" else f"{tweet.tweet_id}_video_{number}.mp4"
                self._record_media(item, os.path.join(target_dir, filename))
            media.append({"type": item.media_type, "url": item.url, "width": item.width, "height": item.height,
                          "filename": item.filename, "order": order, "size_bytes": item.size_bytes})
        payload = {
            "schema_version": METADATA_SCHEMA_VERSION,
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
            "media": media,
            "like_count": tweet.like_count,
            "retweet_count": tweet.retweet_count,
            "reply_count": tweet.reply_count,
        }
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
        # JSON 是作品库的发布边界：所有媒体与正文写完后再替换，完成回调随后才会发出。
        self._atomic_write(os.path.join(target_dir, "tweet.md"), "\n".join(lines))
        self._atomic_write(os.path.join(target_dir, "tweet.json"), json.dumps(payload, ensure_ascii=False, indent=2))
