from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse


ALLOWED_NEWS_HOSTS = frozenset({
    "www.kap.org.tr", "kap.org.tr", "www.aa.com.tr", "aa.com.tr",
    "www.haberturk.com", "haberturk.com", "www.ekonomim.com", "ekonomim.com",
})


def validate_source_url(url: str, base_url: str | None = None) -> str:
    resolved = urljoin(base_url, url) if base_url else url
    parsed = urlparse(resolved)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Güvensiz haber URL'i")
    host = parsed.hostname.rstrip(".").lower()
    if host not in ALLOWED_NEWS_HOSTS:
        raise ValueError("Haber kaynağı allowlist dışında")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ValueError("Private/yerel haber adresi reddedildi")
    return resolved


def validate_redirect(original_url: str, redirected_url: str) -> str:
    validate_source_url(original_url)
    return validate_source_url(redirected_url)
