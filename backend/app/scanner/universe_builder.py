from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.market_data.symbols import BIST100_SYMBOLS, BIST30_EXCLUSION_SYMBOLS


@dataclass(frozen=True)
class UniverseAssessment:
    symbol: str
    status: str
    reason: str
    latest_price: Decimal | None
    affordable: bool


class UniverseBuilder:
    """Provider-owned discovery with a stable, rotating scan order."""

    def __init__(self, provider, config):
        self.provider, self.config = provider, config

    def _ordered_symbols(self) -> list[str]:
        discovered = [str(item).upper().removesuffix(".IS") for item in self.provider.get_symbols()]
        discovered = list(dict.fromkeys(discovered))
        available = set(discovered)

        # Prefer the auditable BIST100 seed, with small/mid names before BIST30 names.
        small_mid = [
            symbol for symbol in BIST100_SYMBOLS
            if symbol in available and symbol not in BIST30_EXCLUSION_SYMBOLS
        ]
        large_caps = [
            symbol for symbol in BIST100_SYMBOLS
            if symbol in available and symbol in BIST30_EXCLUSION_SYMBOLS
        ]
        extras = [symbol for symbol in discovered if symbol not in set(BIST100_SYMBOLS)]
        ordered = small_mid + large_caps + extras
        return ordered or discovered

    def candidates(self, max_symbols: int | None = None, at: datetime | None = None, slot_minutes: int = 15) -> list[str]:
        symbols = self._ordered_symbols()
        if not max_symbols or max_symbols >= len(symbols):
            return symbols

        # Rotate every strategy candle so a bounded batch eventually covers the whole universe.
        slot = int((at or datetime.now(timezone.utc)).astimezone(timezone.utc).timestamp() // (max(1,slot_minutes) * 60))
        start = (slot * max_symbols) % len(symbols)
        end = start + max_symbols
        if end <= len(symbols):
            return symbols[start:end]
        return symbols[start:] + symbols[: end - len(symbols)]

    def assess(self, symbol, frames, portfolio_value: Decimal) -> UniverseAssessment:
        if any(not frames.get(timeframe) for timeframe in ("1d", "1h", "15m")):
            return UniverseAssessment(symbol, "INVALID_DATA", "1D/1H/15M history missing", None, False)
        latest = frames["15m"][-1].close
        complete = all(len(frames[timeframe]) >= 60 for timeframe in ("1d", "1h", "15m"))
        liquid = sum((candle.volume for candle in frames["15m"][-20:]), Decimal(0)) > 0
        budget = portfolio_value * self.config.max_position_size_pct
        affordable = latest <= budget
        if not complete:
            return UniverseAssessment(symbol, "INSUFFICIENT_HISTORY", "Minimum history unavailable", latest, affordable)
        if not liquid:
            return UniverseAssessment(symbol, "ILLIQUID", "Recent 15M volume is zero", latest, affordable)
        if not affordable:
            return UniverseAssessment(symbol, "UNAFFORDABLE", "One lot exceeds max-position budget", latest, False)
        return UniverseAssessment(symbol, "TRADABLE", "Data, liquidity and affordability passed", latest, True)
