from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from app.news.models import NewsRecord
from app.news.security import validate_redirect, validate_source_url


class NewsSource(ABC):
    name = "UNKNOWN"

    def __init__(self, timeout: float = 12, max_bytes: int = 2_000_000, client: httpx.Client | None = None):
        self.timeout, self.max_bytes = timeout, max_bytes
        self.client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "BIST-Paper-Trading-News/1.0 (+public-disclosures; contact=repository-owner)"},
        )

    def fetch_text(self, url: str) -> str:
        safe_url = validate_source_url(url)
        response = self.client.get(safe_url, timeout=self.timeout)
        response.raise_for_status()
        validate_redirect(safe_url, str(response.url))
        body = response.content
        if len(body) > self.max_bytes:
            raise ValueError("Haber yanıtı boyut limitini aşıyor")
        return body.decode(response.encoding or "utf-8", errors="replace")

    @abstractmethod
    def fetch(self) -> list[NewsRecord]: ...
