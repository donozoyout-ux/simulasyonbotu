from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from app.news.models import NewsRecord, infer_symbol
from app.news.security import validate_source_url
from app.news.sources.base import NewsSource


KAP_DISCLOSURES_URL = "https://www.kap.org.tr/tr/bildirim-sorgu"
KAP_CATEGORIES = {
    "finansal": "FINANCIAL_RESULTS", "kar pay": "DIVIDEND", "temett": "DIVIDEND",
    "sermaye art": "CAPITAL_INCREASE", "sermaye azalt": "CAPITAL_DECREASE",
    "geri al": "BUYBACK", "pay işlem": "SHARE_TRANSACTION", "sözleşme": "NEW_CONTRACT",
    "yatırım": "INVESTMENT", "ortaklık": "PARTNERSHIP", "yönetim": "MANAGEMENT_CHANGE",
    "dava": "LEGAL", "hukuk": "LEGAL", "kredi derecel": "CREDIT_RATING",
}


def categorize(title: str) -> str:
    lowered = title.casefold()
    return next((category for needle, category in KAP_CATEGORIES.items() if needle in lowered), "PUBLIC_DISCLOSURE")


def parse_kap_html(payload: str, fetched_at: datetime | None = None) -> list[NewsRecord]:
    """Defensive parser for public disclosure links; layout changes return an empty list, never fabricated data."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    pattern = re.compile(r'<a[^>]+href=["\'](?P<url>/tr/Bildirim/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>', re.I | re.S)
    records: list[NewsRecord] = []
    for match in pattern.finditer(payload):
        title = re.sub(r"<[^>]+>", " ", match.group("title"))
        title = re.sub(r"\s+", " ", html.unescape(title)).strip()
        if not title:
            continue
        url = validate_source_url(match.group("url"), KAP_DISCLOSURES_URL)
        source_id = url.rstrip("/").rsplit("/", 1)[-1]
        records.append(NewsRecord("KAP", source_id, title[:500], "", url, fetched_at, symbol=infer_symbol(title), category=categorize(title)))
    return records


class KapSource(NewsSource):
    name = "KAP"

    def fetch(self) -> list[NewsRecord]:
        payload=self.fetch_text(KAP_DISCLOSURES_URL)
        records=parse_kap_html(payload)
        if "/tr/Bildirim/" in payload and not records:
            raise ValueError("KAP HTML parse edilemedi")
        return records
