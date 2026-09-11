from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from app.market_data.symbols import BIST100_SYMBOLS


def infer_symbol(*texts: str) -> str | None:
    haystack = " ".join(texts).upper()
    return next((symbol for symbol in BIST100_SYMBOLS if re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", haystack)), None)


@dataclass(frozen=True)
class NewsRecord:
    source: str
    source_id: str
    title: str
    content: str
    url: str
    published_at: datetime
    symbol: str | None = None
    company_name: str | None = None
    category: str = "OTHER"
    status: str = "OK"
