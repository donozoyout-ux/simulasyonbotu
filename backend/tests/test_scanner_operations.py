from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import scan_data_health_status, scanner_results, scanner_run, scanner_status, watchlist
from app.config.settings import AppSettings
from app.db.session import Base
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.hybrid_provider import HybridMarketDataProvider
from app.market_data.provider import DataValidationError
from app.models import Analysis, ScanRun, WatchlistItem
from app.scanner.bist_scanner import BistScanner, safe_provider_error
from app.scanner.universe_builder import UniverseBuilder
from app.scanner.watchlist_manager import update_watchlist
from app.services.forward_test import ensure_forward_run


def database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def settings(**changes):
    values = {
        "data_mode": "mock",
        "market_data_provider": "mock",
        "operation_mode": "LIVE_PAPER",
        "ai_enabled": False,
        "telegram_enabled": False,
    }
    values.update(changes)
    return AppSettings(_env_file=None, **values)


def analysis(run_id: str, symbol: str, score: int) -> Analysis:
    return Analysis(
        symbol=symbol,
        price=Decimal("100"),
        score=score,
        trend="neutral",
        market_structure="RANGE",
        setup="NONE",
        decision="WATCH",
        reason="threshold not reached",
        details={},
        data_source="mock",
        signal_candle_time=datetime.now(timezone.utc),
        data_valid=True,
        run_id=run_id,
    )


def scan_row(run_id: str, *, valid: int, failed: int, stale: int = 0, complete: bool = True) -> ScanRun:
    now = datetime.now(timezone.utc)
    return ScanRun(
        started_at=now - timedelta(seconds=2),
        completed_at=now if complete else None,
        duration_ms=2000 if complete else None,
        provider="mock",
        data_mode="mock",
        total_symbols=valid + failed,
        valid_symbols=valid,
        failed_symbols=failed,
        stale_symbols=stale,
        funnel={"total": valid + failed},
        errors=[{"symbol": "FAIL", "error": "Yahoo timeout"}] if failed else [],
        run_id=run_id,
        watchlist_count=0,
        signals=0,
        entries=0,
    )


def test_scanner_status_no_scan_and_completed_with_errors():
    cfg = settings()
    with Session(database()) as db, patch("app.api.routes.config", cfg):
        run = ensure_forward_run(db, cfg)
        empty = scanner_status(db)
        assert empty["status"] == "NO_SCAN"
        assert empty["watchlist_score"] == 70 and empty["entry_score"] == 82

        db.add(scan_row(run.run_id, valid=9, failed=1))
        db.commit()
        completed = scanner_status(db)
        assert completed["status"] == "COMPLETE_WITH_ERRORS"
        assert completed["last_scan"]["valid_symbols"] == 9
        assert completed["last_scan"]["errors"][0]["symbol"] == "FAIL"


def test_data_health_distinguishes_partial_from_critical_failure():
    assert scan_data_health_status(scan_row("run", valid=10, failed=0)) == "OK"
    assert scan_data_health_status(scan_row("run", valid=9, failed=1)) == "PARTIAL"
    assert scan_data_health_status(scan_row("run", valid=1, failed=4)) == "DATA_ERROR"
    assert scan_data_health_status(scan_row("run", valid=10, failed=0, stale=1)) == "PARTIAL"


def test_manual_scan_honors_ten_and_caps_oversized_batch():
    cfg = settings(data_mode="live", market_data_provider="yahoo")
    result = {"status": "completed", "analyzed": 10, "errors": [], "results": []}
    with Session(database()) as db, patch("app.api.routes.config", cfg), \
            patch("app.api.routes.BistMarketSession.is_open", return_value=True), \
            patch("app.api.routes.BistScanner") as scanner:
        scanner.return_value.run.return_value = result
        assert scanner_run(10, db)["analyzed"] == 10
        scanner.return_value.run.assert_called_with(10)
        scanner_run(100, db)
        scanner.return_value.run.assert_called_with(30)


def test_rotation_is_deterministic_and_eventually_covers_universe():
    symbols = [f"S{index}" for index in range(7)]
    provider = SimpleNamespace(get_symbols=lambda: symbols)
    builder = UniverseBuilder(provider, settings())
    start = datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc)
    assert builder.candidates(3, start) == builder.candidates(3, start)
    covered = {
        symbol
        for step in range(len(symbols))
        for symbol in builder.candidates(3, start + timedelta(minutes=15 * step))
    }
    assert covered == set(symbols)


def test_current_run_filters_results_and_watchlist_without_hiding_low_scores():
    cfg = settings()
    with Session(database()) as db, patch("app.api.routes.config", cfg):
        run = ensure_forward_run(db, cfg)
        db.add_all([
            analysis(run.run_id, "LOW", 42),
            analysis("OLD-RUN", "OLD", 99),
            WatchlistItem(symbol="CURRENT", score=70, setup="NONE", status="WATCHING", reason="ok", run_id=run.run_id),
            WatchlistItem(symbol="OLDWATCH", score=95, setup="BREAKOUT", status="POSSIBLE_ENTRY", reason="old", run_id="OLD-RUN"),
        ])
        db.commit()
        assert [(row["symbol"], row["score"]) for row in scanner_results(db)] == [("LOW", 42)]
        assert [row["symbol"] for row in watchlist(db)] == ["CURRENT"]


def test_watchlist_thresholds_remain_70_and_82():
    with Session(database()) as db:
        assert update_watchlist(db, "LOW", 69, "NONE", "low", 70, 82, run_id="run") is None
        item = update_watchlist(db, "WATCH", 70, "BREAKOUT", "watch", 70, 82, run_id="run")
        assert item is not None and item.status == "WATCHING"
        entry = update_watchlist(db, "ENTRY", 82, "BREAKOUT", "entry", 70, 82, run_id="run")
        assert entry is not None and entry.status == "POSSIBLE_ENTRY"


class PartialProvider(MockMarketDataProvider):
    name = "mock"

    def get_symbols(self):
        return ["FAIL", "GOOD"]

    def get_candles(self, symbol, timeframe, limit=200):
        if symbol == "FAIL":
            raise DataValidationError("Yahoo timeout apikey=must-not-leak")
        return super().get_candles(symbol, timeframe, limit)


def test_partial_symbol_failure_does_not_abort_scan_and_redacts_secret():
    cfg = settings()
    with Session(database()) as db:
        result = BistScanner(db, cfg, PartialProvider(), require_market_session=False).run(2)
        assert result["status"] == "completed" and result["analyzed"] == 1
        assert result["errors"] == [{"symbol": "FAIL", "error": "Yahoo timeout apikey=***"}]
        run = db.scalar(select(ScanRun))
        assert run.valid_symbols == 1 and run.failed_symbols == 1
        assert db.scalar(select(Analysis).where(Analysis.symbol == "GOOD")) is not None
    assert "secret" not in safe_provider_error(RuntimeError("api_token=secret"))


def test_bulk_hybrid_failure_never_spends_twelve_quota():
    calls = []
    provider = HybridMarketDataProvider.__new__(HybridMarketDataProvider)
    provider.yahoo = SimpleNamespace(get_candles=lambda *_args: (_ for _ in ()).throw(RuntimeError("Yahoo timeout")))
    provider.twelve = SimpleNamespace(get_candles=lambda *_args: calls.append(1))
    provider.last_sources = {}
    provider.last_validation = {}
    try:
        provider._fetch_candles("ASELS", "15m", 220)
    except DataValidationError as exc:
        assert "Yahoo timeout" in str(exc)
    else:
        raise AssertionError("Bulk Yahoo failure must be explicit")
    assert calls == []
