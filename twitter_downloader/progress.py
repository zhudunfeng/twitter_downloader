from __future__ import annotations

from typing import Protocol


class ProgressHandler(Protocol):
    def on_start(self, url: str) -> None:
        ...

    def on_progress(self, percent: float, status: str) -> None:
        ...

    def on_log(self, message: str) -> None:
        ...

    def on_done(self, path: str) -> None:
        ...

    def on_error(self, url: str, error: str) -> None:
        ...
