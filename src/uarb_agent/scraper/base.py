from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import MatterRequest, ScrapeResult


class Scraper(Protocol):
    """Anything that can turn a request into a :class:`ScrapeResult` (real portal or fake)."""

    def fetch(self, request: MatterRequest, out_dir: Path, limit: int) -> ScrapeResult: ...
