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

    def download(
        self,
        url: str,
        output_dir: str = "downloads",
        *,
        cookies: str | None = None,
        verbose: bool = False,
    ) -> str:
        if not is_tweet_url(url):
            raise ValueError(f"Invalid Twitter/X URL: {url}")
        ensure_dir(output_dir)
        tweet = YtDlpTweetExtractor(cookies=cookies).extract(url)
        return Downloader(
            output_path_builder=OutputPathBuilder(base_dir=output_dir),
            cookies=cookies,
            verbose=verbose,
        ).download(tweet)

    def batch(
        self,
        urls: List[str],
        output_dir: str = "downloads",
        *,
        cookies: str | None = None,
        verbose: bool = False,
    ) -> List[str]:
        results: List[str] = []
        for url in (value.strip() for value in urls):
            if not url:
                continue
            try:
                results.append(self.download(url, output_dir, cookies=cookies, verbose=verbose))
            except Exception as exc:
                self._logger.error("Download failed %s: %s", url, exc)
        return results


@click.group()
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def app(ctx: click.Context, verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ctx.ensure_object(dict)
    ctx.obj["client"] = MediaDownloader()
    ctx.obj["verbose"] = verbose


@app.command()
@click.argument("url")
@click.option("--output", "-o", default="downloads", show_default=True)
@click.option("--cookies", default=None, help='Cookie file or browser="chrome".')
@click.option("--verbose", is_flag=True, help="Enable yt-dlp debug logging.")
@click.pass_context
def single(ctx: click.Context, url: str, output: str, cookies: str | None, verbose: bool) -> None:
    try:
        destination = ctx.obj["client"].download(
            url,
            output,
            cookies=cookies,
            verbose=verbose or ctx.obj["verbose"],
        )
        click.echo(f"Saved to: {destination}")
    except Exception as exc:
        click.echo(f"Failed: {exc}", err=True)
        sys.exit(1)


@app.command()
@click.argument("input_path")
@click.option("--output", "-o", default="downloads", show_default=True)
@click.option("--cookies", default=None, help='Cookie file or browser="chrome".')
@click.option("--verbose", is_flag=True, help="Enable yt-dlp debug logging.")
@click.pass_context
def batch(ctx: click.Context, input_path: str, output: str, cookies: str | None, verbose: bool) -> None:
    try:
        with open(input_path, "r", encoding="utf-8") as handle:
            urls = [line.strip() for line in handle if line.strip()]
    except OSError as exc:
        click.echo(f"Could not read input file: {exc}", err=True)
        sys.exit(1)
    if not urls:
        click.echo("No URLs found.")
        return
    results = ctx.obj["client"].batch(
        urls,
        output,
        cookies=cookies,
        verbose=verbose or ctx.obj["verbose"],
    )
    click.echo(f"Completed: {len(results)} item(s).")
