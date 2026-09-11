import hashlib
import re


def canonical_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def content_hash(source: str, title: str, content: str, url: str) -> str:
    canonical = "|".join(map(canonical_text, (title, content)))
    if not canonical.strip("|"):
        canonical = canonical_text(url)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
