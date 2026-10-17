from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Iterable

import click

from twitter_downloader.core import download_one


@dataclass(frozen=True)
class BatchFailure:
    url: str
    error: str


@dataclass
class BatchDownloadResult:
    destinations: list[str] = field(default_factory=list)
    failures: list[BatchFailure] = field(default_factory=list)


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
        use_authenticated: bool = True,
    ) -> str:
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
        result = BatchDownloadResult()
        seen_urls: set[str] = set()
        for value in urls:
            url = value.strip()
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
                self._logger.error("Download failed %s: %s", url, exc)
                result.failures.append(BatchFailure(url=url, error=str(exc)))
        return result


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
    try:
        destination = ctx.obj["client"].download(
            url,
            output,
            cookies=cookies,
            verbose=verbose or ctx.obj["verbose"],
            use_authenticated=not anonymous,
        )
        click.echo(f"Saved to: {destination}")
    except Exception as exc:
        click.echo(f"Failed: {exc}", err=True)
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
    try:
        with open(input_path, "r", encoding="utf-8") as handle:
            urls = [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]
    except OSError as exc:
        click.echo(f"Could not read input file: {exc}", err=True)
        sys.exit(1)
    if not urls:
        click.echo("No URLs found.")
        return
    result = ctx.obj["client"].batch(
        urls,
        output,
        cookies=cookies,
        verbose=verbose or ctx.obj["verbose"],
        use_authenticated=not anonymous,
    )
    click.echo(f"Completed: {len(result.destinations)} succeeded, {len(result.failures)} failed.")
    for failure in result.failures:
        click.echo(f"Failed: {failure.url}: {failure.error}", err=True)
    if result.failures:
        raise click.exceptions.Exit(1)
