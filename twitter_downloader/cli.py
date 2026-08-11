from __future__ import annotations

import logging
import sys
from typing import List

import click

from twitter_downloader.downloader import Downloader
from twitter_downloader.extractor import YtDlpTweetExtractor
from twitter_downloader.output import OutputPathBuilder
from twitter_downloader.utils import ensure_dir, is_tweet_url


class MediaDownloader:
    def __init__(self) -> None:
        self._logger = logging.getLogger("twitter_downloader.cli")

    def download(self, url: str, output_dir: str = "downloads") -> str:
        if not is_tweet_url(url):
            raise ValueError(f"无效的 Twitter/X 链接: {url}")
        ensure_dir(output_dir)
        output_builder = OutputPathBuilder(base_dir=output_dir)
        extractor = YtDlpTweetExtractor()
        downloader = Downloader(output_path_builder=output_builder)
        tweet_info = extractor.extract(url)
        dest_dir = downloader.download(tweet_info)
        return dest_dir

    def batch(self, urls: List[str], output_dir: str = "downloads") -> List[str]:
        results: List[str] = []
        for url in urls:
            url = url.strip()
            if not url:
                continue
            try:
                result = self.download(url, output_dir=output_dir)
                results.append(result)
            except Exception as e:
                self._logger.error("下载失败 %s -> %s", url, e)
        return results


@click.group()
@click.option("--verbose", is_flag=True, help="启用调试日志")
@click.pass_context
def app(ctx: click.Context, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    ctx.ensure_object(dict)
    ctx.obj["client"] = MediaDownloader()


@app.command()
@click.argument("url")
@click.option("--output", "-o", default="downloads", show_default=True)
@click.option("--cookies", default=None, help='cookies 文件或 browser="chrome"')
@click.option("--verbose", is_flag=True, help="是否启用调试日志")
@click.pass_context
def single(ctx: click.Context, url: str, output: str, cookies: str | None, verbose: bool) -> None:
    client: MediaDownloader = ctx.obj["client"]
    try:
        dest = client.download(url, output_dir=output)
        click.echo(f"已保存到: {dest}")
    except Exception as e:
        click.echo(f"失败: {e}", err=True)
        sys.exit(1)


@app.command()
@click.argument("input_path")
@click.option("--output", "-o", default="downloads", show_default=True)
@click.pass_context
def batch(ctx: click.Context, input_path: str, output: str) -> None:
    client: MediaDownloader = ctx.obj["client"]
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip()]
    except OSError as e:
        click.echo(f"读取失败: {e}", err=True)
        sys.exit(1)
    if not urls:
        click.echo("未发现待处理链接")
        sys.exit(0)
    results = client.batch(urls, output_dir=output)
    click.echo(f"完成，共 {len(results)} 项")
