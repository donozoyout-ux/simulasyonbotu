from dataclasses import dataclass

from app.news.sources import GenericRssSource, KapSource


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    type: str
    base_url: str
    enabled: bool
    priority: int
    poll_interval: int
    supports_backfill: bool
    supports_symbol_mapping: bool


SOURCE_REGISTRY = (
    SourceSpec("KAP", "Kamuyu Aydınlatma Platformu", "OFFICIAL", "https://www.kap.org.tr/tr/bildirim-sorgu", True, 10, 300, False, True),
    SourceSpec("AA", "Anadolu Ajansı Ekonomi", "RSS", "https://www.aa.com.tr/tr/rss/default?cat=ekonomi", True, 30, 300, False, True),
    SourceSpec("HABERTURK", "Habertürk Ekonomi", "RSS", "https://www.haberturk.com/rss/ekonomi.xml", True, 40, 300, False, True),
    SourceSpec("EKONOMIM", "Ekonomim", "RSS", "https://www.ekonomim.com/rss", True, 50, 300, False, True),
)


def source_spec(source_id: str):
    return next((item for item in SOURCE_REGISTRY if item.id == source_id), None)


def build_sources(config):
    common = {"timeout": config.news_http_timeout_seconds, "max_bytes": config.news_max_html_bytes}
    sources = []
    for spec in sorted(SOURCE_REGISTRY, key=lambda item: item.priority):
        if not spec.enabled or (spec.id == "KAP" and not config.kap_enabled): continue
        source = KapSource(**common) if spec.id == "KAP" else GenericRssSource(spec.id, spec.base_url, **common)
        source.spec = spec; sources.append(source)
    return sources
