from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.news.company_aliases import map_company_symbol


def infer_symbol(*texts: str) -> str | None:
    return map_company_symbol(*texts)[0]


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
    source_metadata: dict | None = None
