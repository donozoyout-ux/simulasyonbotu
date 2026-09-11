from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from app.news.models import NewsRecord, infer_symbol
from app.news.security import validate_source_url
from app.news.sources.base import NewsSource


PUBLIC_RSS_URL = "https://www.aa.com.tr/tr/rss/default?cat=ekonomi"


def parse_rss(payload: str, source: str = "AA") -> list[NewsRecord]:
    root = ElementTree.fromstring(payload)
    records = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        url = validate_source_url((item.findtext("link") or "").strip(), PUBLIC_RSS_URL)
        if not title or not url:
            continue
        raw_date = (item.findtext("pubDate") or "").strip()
        try:
            published = parsedate_to_datetime(raw_date).astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = datetime.now(timezone.utc)
        source_id = (item.findtext("guid") or url).strip()
        content=item.findtext("description") or ""
        records.append(NewsRecord(source, source_id[:200], title[:500], content, url, published, symbol=infer_symbol(title,content)))
    return records


class RssSource(NewsSource):
    name = "RSS"

    def fetch(self) -> list[NewsRecord]:
        # General economy feeds are filtered locally; unrelated stories never consume Groq quota.
        return [record for record in parse_rss(self.fetch_text(PUBLIC_RSS_URL)) if record.symbol]
