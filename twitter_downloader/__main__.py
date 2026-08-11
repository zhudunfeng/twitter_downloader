from __future__ import annotations

import sys

from twitter_downloader.cli import app
from twitter_downloader.utils import is_tweet_url


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments and is_tweet_url(arguments[0]):
        app(args=["single", *arguments])
    else:
        app()
