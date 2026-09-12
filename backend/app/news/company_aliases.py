from __future__ import annotations

import re
import unicodedata

from app.market_data.symbols import BIST100_SYMBOLS


COMPANY_ALIASES = {
    "THYAO": ("türk hava yolları", "turk hava yollari", "turkish airlines"),
    "ASELS": ("aselsan",), "TUPRS": ("tüpraş", "tupras"), "EREGL": ("ereğli demir çelik", "erdemir"),
    "KCHOL": ("koç holding", "koc holding"), "SAHOL": ("sabancı holding", "sabanci holding"),
    "GARAN": ("garanti bbva", "garanti bankası"), "AKBNK": ("akbank",), "YKBNK": ("yapı kredi", "yapi kredi"),
    "ISCTR": ("türkiye iş bankası", "is bankasi"), "TCELL": ("turkcell",), "TTKOM": ("türk telekom", "turk telekom"),
    "BIMAS": ("bim birleşik mağazalar",), "MGROS": ("migros",), "SOKM": ("şok marketler", "sok marketler"),
    "BIZIM": ("bizim toptan",), "FROTO": ("ford otosan",), "TOASO": ("tofaş", "tofas"),
    "ARCLK": ("arçelik", "arcelik"), "SISE": ("şişecam", "sisecam"), "PETKM": ("petkim",),
    "PGSUS": ("pegasus hava yolları", "pegasus airlines"), "TAVHL": ("tav havalimanları", "tav airports"),
    "ENKAI": ("enka inşaat", "enka insaat"), "EKGYO": ("emlak konut",), "KOZAL": ("koza altın", "koza altin"),
    "KRDMD": ("kardemir",), "VESTL": ("vestel",), "ULKER": ("ülker bisküvi", "ulker biskuvi"),
    "AEFES": ("anadolu efes",), "CCOLA": ("coca-cola içecek", "coca cola icecek"),
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(ch for ch in value if not unicodedata.combining(ch)).split())


def map_company_symbol(*texts: str) -> tuple[str | None, str | None]:
    original = " ".join(texts)
    explicit = {ticker for ticker in BIST100_SYMBOLS if re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", original)}
    normalized = _normalize(original)
    matches = set(explicit)
    matched_name = None
    for ticker, aliases in COMPANY_ALIASES.items():
        for alias in aliases:
            if re.search(rf"(?<!\w){re.escape(_normalize(alias))}(?!\w)", normalized):
                matches.add(ticker); matched_name = alias; break
    if len(matches) != 1:
        return None, None
    return next(iter(matches)), matched_name
