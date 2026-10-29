"""Browser automation for the UARB FileMaker WebDirect portal."""

from .base import Scraper
from .uarb import UarbScraper

__all__ = ["Scraper", "UarbScraper"]
