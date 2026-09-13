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
from app.models import Analysis, Order, ScanRun, SymbolHealth, WatchlistItem
from app.scanner.bist_scanner import BistScanner, classify_provider_error, safe_provider_error
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
        assert result["errors"][0] == {
            "symbol":"FAIL","type":"PROVIDER_TIMEOUT","status":"PROVIDER_ERROR",
            "message":"Yahoo timeout apikey=***","error":"Yahoo timeout apikey=***",
            "consecutive_failures":1,"retry_at":None,
        }
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


def entry_item(symbol="GOOD"):
    analysis=SimpleNamespace(decision="POSSIBLE_ENTRY",symbol=symbol,setup="BREAKOUT",score=90,price=Decimal("55"),
        reason="valid candidate",data_valid=True,data_source="yahoo",details={"analysis_complete":True,"risk_reward":Decimal("3"),
            "setup":{"entry_area":55,"invalidation_level":50,"target":70},"volatility":{"atr":Decimal("2")}})
    result=SimpleNamespace(signal_candle_time=datetime(2026,9,11,9,tzinfo=timezone.utc),
        funnel={"data_valid":True,"sufficient_history":True,"htf_bullish":True,"valid_setup":True,"score_pass":True,"rr_pass":True})
    assessment=SimpleNamespace(status="TRADABLE",affordable=True,reason="ok")
    return analysis,result,assessment


def test_one_symbol_failure_does_not_disable_valid_live_entry():
    cfg=settings(data_mode="live",market_data_provider="yahoo")
    provider=SimpleNamespace(name="yahoo",get_symbols=lambda:["FAIL","GOOD"])
    at=datetime(2026,9,11,9,15,tzinfo=timezone.utc)
    with Session(database()) as db:
        scanner=BistScanner(db,cfg,provider,analysis_mode="LIVE",market_open=True,analysis_at=at)
        with patch.object(scanner,"analyze_symbol",side_effect=[DataValidationError("UMPAS yetersiz kapanmış mum"),entry_item()]), \
                patch.object(scanner,"_manage_positions"),patch("app.scanner.bist_scanner.BistMarketSession.is_open",return_value=True):
            result=scanner.run(2)
        assert result["analyzed"]==1 and len(result["errors"])==1
        assert result["funnel"]["symbols_failed"]==1 and result["funnel"]["entry_eligible"]==1
        assert result["funnel"]["entry_blocked_global"]==0 and result["funnel"]["buy"]==1
        assert db.scalar(select(Order).where(Order.symbol=="GOOD")) is not None
        assert db.scalar(select(Order).where(Order.symbol=="FAIL")) is None


def test_open_market_partial_batch_22_valid_8_failed_keeps_candidate_eligible():
    cfg=settings(data_mode="live",market_data_provider="yahoo")
    symbols=[f"S{index:02d}" for index in range(30)]
    provider=SimpleNamespace(name="yahoo",get_symbols=lambda:symbols)
    at=datetime(2026,9,11,9,15,tzinfo=timezone.utc)
    def analyze(symbol,_at):
        if int(symbol[1:])<8:raise DataValidationError(f"{symbol} Yahoo timeout")
        if symbol=="S08":return entry_item(symbol)
        item=entry_item(symbol);item[0].decision="NO_TRADE";item[0].score=40
        return item
    with Session(database()) as db:
        scanner=BistScanner(db,cfg,provider,analysis_mode="LIVE",market_open=True,analysis_at=at)
        with patch.object(scanner,"analyze_symbol",side_effect=analyze),patch.object(scanner,"_manage_positions"), \
                patch("app.scanner.bist_scanner.BistMarketSession.is_open",return_value=True):
            result=scanner.run(30)
        assert result["funnel"]["symbols_requested"]==30
        assert result["funnel"]["symbols_analyzed"]==22 and result["funnel"]["symbols_failed"]==8
        assert result["funnel"]["entry_eligible"]==1 and result["funnel"]["entry_blocked_global"]==0
        assert result["funnel"]["buy"]==1 and db.scalar(select(Order).where(Order.symbol=="S08")) is not None


def test_global_config_or_session_gate_blocks_all_candidates():
    cfg=settings(data_mode="live",market_data_provider="yahoo")
    provider=SimpleNamespace(name="yahoo")
    with Session(database()) as db:
        scanner=BistScanner(db,cfg,provider);scanner.forward_run=ensure_forward_run(db,cfg)
        funnel={"risk_pass":0,"buy":0}
        with patch("app.scanner.bist_scanner.BistMarketSession.is_open",return_value=False):
            scanner._try_entries([entry_item()],funnel,market_open=False)
        assert funnel["entry_blocked_global"]==1 and funnel["buy"]==0
        assert db.scalar(select(Order)) is None


def test_global_config_mismatch_blocks_all_candidates():
    cfg=settings(data_mode="live",market_data_provider="yahoo")
    with Session(database()) as db:
        scanner=BistScanner(db,cfg,SimpleNamespace(name="yahoo"));scanner.forward_run=ensure_forward_run(db,cfg)
        scanner.forward_run.strategy_config_hash="mismatch";db.commit();funnel={"risk_pass":0,"buy":0}
        with patch("app.scanner.bist_scanner.BistMarketSession.is_open",return_value=True):
            scanner._try_entries([entry_item()],funnel,market_open=True)
        assert funnel["entry_blocked_global"]==1 and db.scalar(select(Order)) is None


def test_risk_engine_failure_becomes_global_block():
    cfg=settings(data_mode="live",market_data_provider="yahoo")
    with Session(database()) as db:
        scanner=BistScanner(db,cfg,SimpleNamespace(name="yahoo"));scanner.forward_run=ensure_forward_run(db,cfg)
        funnel={"risk_pass":0,"buy":0}
        with patch("app.scanner.bist_scanner.BistMarketSession.is_open",return_value=True), \
                patch("app.scanner.bist_scanner.size_position",side_effect=RuntimeError("risk unavailable")):
            scanner._try_entries([entry_item()],funnel,market_open=True)
        assert funnel["entry_blocked_global"]==1 and funnel["buy"]==0


def test_symbol_quarantine_and_expiry_and_replacement():
    cfg=settings(symbol_quarantine_failure_threshold=3,symbol_quarantine_minutes=60)
    at=datetime(2026,9,11,9,tzinfo=timezone.utc)
    provider=SimpleNamespace(name="yahoo",get_symbols=lambda:["BAD","S1","S2","S3"])
    with Session(database()) as db:
        scanner=BistScanner(db,cfg,provider)
        for _ in range(3):scanner._record_symbol_failure("BAD",DataValidationError("Yahoo timeout"),at)
        health=db.get(SymbolHealth,"BAD")
        assert health.consecutive_failures==3 and "BAD" in scanner._quarantined_symbols(at+timedelta(minutes=59))
        assert "BAD" not in scanner._quarantined_symbols(at+timedelta(minutes=61))
        selected=scanner.universe.candidates(3,at,excluded={"BAD"})
        assert len(selected)==3 and "BAD" not in selected


def test_unavailable_symbol_types_are_explicit():
    assert classify_provider_error(DataValidationError("UMPAS yetersiz kapanmış mum"))=="INSUFFICIENT_HISTORY"
    assert classify_provider_error(DataValidationError("UTPYA Yahoo 404 Not Found"))=="SYMBOL_NOT_FOUND"


def test_provider_error_classification_contract():
    cases={"request timed out":"PROVIDER_TIMEOUT","HTTP 429":"PROVIDER_RATE_LIMIT","404 Not Found":"SYMBOL_NOT_FOUND",
        "insufficient history":"INSUFFICIENT_HISTORY","stale data":"STALE_DATA","invalid OHLC":"INVALID_OHLC",
        "zero volume":"ZERO_VOLUME","socket broke":"UNKNOWN"}
    assert {message:classify_provider_error(RuntimeError(message)) for message in cases}==cases


def test_scanner_status_exposes_score_distribution():
    cfg=settings()
    with Session(database()) as db,patch("app.api.routes.config",cfg):
        run=ensure_forward_run(db,cfg)
        row=scan_row(run.run_id,valid=3,failed=1)
        row.funnel={"score_highest":88,"score_average":72.5,"score_above_watchlist":2,"score_above_entry":1}
        db.add(row);db.commit()
        stats=scanner_status(db)["score_stats"]
        assert stats=={"highest":88,"average":72.5,"above_watchlist":2,"above_entry":1}
