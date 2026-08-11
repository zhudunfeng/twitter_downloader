from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class OutputPathBuilder:
    def __init__(self, base_dir: str = "downloads") -> None:
        self.base_dir = base_dir

    def tweet_dir(self, username: str, created_at: Optional[datetime], tweet_id: str) -> str:
        user_key = self._safe(username)
        date_key = created_at.strftime("%Y%m%d") if created_at else datetime.now().strftime("%Y%m%d")
        return os.path.join(self.base_dir, user_key, date_key, tweet_id)

    @staticmethod
    def _safe(value: str) -> str:
        value = value.strip() or "unknown"
        value = value.replace("\\", "/").split("/")[-1]
        return value
