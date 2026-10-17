from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Iterable

import click

from twitter_downloader.core import download_one
from twitter_downloader.logging_setup import get_log_path, get_logger, setup_logging

# CLI 模块日志器，输出同时到达控制台与日志文件
_logger = get_logger("cli")


@dataclass(frozen=True)
class BatchFailure:
    """批量下载中的单条失败记录。"""

    url: str
    error: str


@dataclass
class BatchDownloadResult:
    """批量下载结果汇总。"""

    destinations: list[str] = field(default_factory=list)
    failures: list[BatchFailure] = field(default_factory=list)


class MediaDownloader:
    """CLI 使用的下载门面，负责单条与批量下载。"""

    def __init__(self) -> None:
        # 模块级 logger，日志会同时写入控制台与文件
        self._logger = get_logger("cli")

    def download(
        self,
        url: str,
        output_dir: str = "downloads",
        *,
        cookies: str | None = None,
        verbose: bool = False,
        use_authenticated: bool = True,
    ) -> str:
        """下载单条推文，返回落盘目录。"""
        return download_one(
            url,
            output_dir,
            cookies=cookies,
            verbose=verbose,
            use_authenticated=use_authenticated,
        )

    def batch(
        self,
        urls: Iterable[str],
        output_dir: str = "downloads",
        *,
        cookies: str | None = None,
        verbose: bool = False,
        use_authenticated: bool = True,
    ) -> BatchDownloadResult:
        """按顺序批量下载，单条失败不影响后续任务。"""
        result = BatchDownloadResult()
        # 已处理过的链接集合，用于去重
        seen_urls: set[str] = set()
        for value in urls:
            url = value.strip()
            # 跳过空行、注释行与重复链接
            if not url or url.startswith("#") or url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                result.destinations.append(
                    self.download(
                        url,
                        output_dir,
                        cookies=cookies,
                        verbose=verbose,
                        use_authenticated=use_authenticated,
                    )
                )
            except Exception as exc:
                # 记录失败但继续处理后续链接
                self._logger.error("下载失败 %s: %s", url, exc)
                result.failures.append(BatchFailure(url=url, error=str(exc)))
        return result


@click.group()
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
@click.option("--log-file", default=None, help="Path of the log file (defaults to the app data directory).")
@click.pass_context
def app(ctx: click.Context, verbose: bool, log_file: str | None) -> None:
    """命令行入口：初始化控制台与文件双路日志。"""
    log_path = setup_logging(verbose=verbose, log_path=log_file)
    if verbose:
        click.echo(f"日志文件: {log_path}")
    ctx.ensure_object(dict)
    ctx.obj["client"] = MediaDownloader()
    ctx.obj["verbose"] = verbose


@app.command()
@click.argument("url")
@click.option("--output", "-o", default="downloads", show_default=True)
@click.option("--cookies", default=None, help='Cookie file, browser="chrome", or "auto" to auto-detect.')
@click.option("--anonymous", is_flag=True, help="Use the anonymous syndication endpoint instead of the authenticated API.")
@click.option("--verbose", is_flag=True, help="Enable yt-dlp debug logging.")
@click.pass_context
def single(
    ctx: click.Context,
    url: str,
    output: str,
    cookies: str | None,
    anonymous: bool,
    verbose: bool,
) -> None:
    """下载单条推文。"""
    try:
        destination = ctx.obj["client"].download(
            url,
            output,
            cookies=cookies,
            verbose=verbose or ctx.obj["verbose"],
            use_authenticated=not anonymous,
        )
        click.echo(f"已保存到: {destination}")
        click.echo(f"日志文件: {get_log_path()}")
    except Exception as exc:
        click.echo(f"下载失败: {exc}", err=True)
        click.echo(f"日志文件: {get_log_path()}", err=True)
        sys.exit(1)


@app.command()
@click.argument("input_path")
@click.option("--output", "-o", default="downloads", show_default=True)
@click.option("--cookies", default=None, help='Cookie file, browser="chrome", or "auto" to auto-detect.')
@click.option("--anonymous", is_flag=True, help="Use the anonymous syndication endpoint instead of the authenticated API.")
@click.option("--verbose", is_flag=True, help="Enable yt-dlp debug logging.")
@click.pass_context
def batch(
    ctx: click.Context,
    input_path: str,
    output: str,
    cookies: str | None,
    anonymous: bool,
    verbose: bool,
) -> None:
    """批量下载文本文件中的推文链接。"""
    try:
        with open(input_path, "r", encoding="utf-8") as handle:
            urls = [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]
    except OSError as exc:
        _logger.error("无法读取输入文件 %s: %s", input_path, exc)
        click.echo(f"无法读取输入文件: {exc}", err=True)
        sys.exit(1)
    if not urls:
        click.echo("未找到任何链接。")
        return
    _logger.info("批量下载开始，共 %d 条链接", len(urls))
    result = ctx.obj["client"].batch(
        urls,
        output,
        cookies=cookies,
        verbose=verbose or ctx.obj["verbose"],
        use_authenticated=not anonymous,
    )
    _logger.info("批量下载结束：成功 %d，失败 %d", len(result.destinations), len(result.failures))
    click.echo(f"完成：成功 {len(result.destinations)} 条，失败 {len(result.failures)} 条。")
    for failure in result.failures:
        click.echo(f"失败: {failure.url}: {failure.error}", err=True)
    click.echo(f"日志文件: {get_log_path()}")
    if result.failures:
        raise click.exceptions.Exit(1)
