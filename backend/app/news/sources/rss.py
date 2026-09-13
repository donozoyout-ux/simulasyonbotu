from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from app.news.company_aliases import resolve_company_symbols
from app.news.models import NewsRecord,infer_symbol
from app.news.security import validate_source_url
from app.news.sources.base import NewsSource


PUBLIC_RSS_URL = "https://www.aa.com.tr/tr/rss/default?cat=ekonomi"


def parse_rss(payload: str, source: str = "AA", base_url: str = PUBLIC_RSS_URL) -> list[NewsRecord]:
    root = ElementTree.fromstring(payload)
    records = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        url = validate_source_url((item.findtext("link") or "").strip(), base_url)
        if not title or not url:
            continue
        raw_date = (item.findtext("pubDate") or "").strip()
        try:
            published = parsedate_to_datetime(raw_date).astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = datetime.now(timezone.utc)
        source_id = (item.findtext("guid") or url).strip()
        content=item.findtext("description") or ""
        categories=[(node.text or "").strip() for node in item.findall("category") if (node.text or "").strip()]
        metadata={"guid":source_id[:200],"categories":categories,"author":(item.findtext("author") or "").strip()}
        records.append(NewsRecord(source, source_id[:200], title[:500], content, url, published,
            symbol=infer_symbol(title,content),source_metadata=metadata,symbol_is_structured=False))
    return records


class RssSource(NewsSource):
    name = "RSS"

    def fetch(self) -> list[NewsRecord]:
        # General economy feeds are filtered locally; unrelated stories never consume Groq quota.
        return [record for record in parse_rss(self.fetch_text(PUBLIC_RSS_URL))
            if resolve_company_symbols(record.title,record.content).primary_symbol]


class GenericRssSource(NewsSource):
    def __init__(self, source_id: str, feed_url: str, **kwargs):
        super().__init__(**kwargs); self.name, self.feed_url = source_id, feed_url

    def fetch(self) -> list[NewsRecord]:
        return parse_rss(self.fetch_text(self.feed_url), self.name, self.feed_url)
