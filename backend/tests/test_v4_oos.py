from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config.settings import AppSettings
from app.market_data.eodhd_provider import EodhdHistoricalProvider
from app.market_data.market_session import BistMarketSession
from app.market_data.provider import CandleData
from app.research.v4 import (V3_RESEARCH_END, assert_oos_period, audit_dataset, bootstrap_summary,
                             daily_session_audit, percent_from_fraction, position_size_diagnostics,
                             strategy_config_snapshot)


def candle(at, price=100):
    value = Decimal(str(price))
    return CandleData(at, value, value + 1, value - 1, value, Decimal("1000"), True)


def frames():
    start = datetime(2026, 1, 5, 7, tzinfo=timezone.utc)
    return {"TEST": {
        "15m": [candle(start + timedelta(minutes=15 * i)) for i in range(100)],
        "1h": [candle(start - timedelta(days=80) + timedelta(hours=i)) for i in range(100)],
        "1d": [candle(start - timedelta(days=100) + timedelta(days=i)) for i in range(100)],
    }}


def test_warmup_rows_are_excluded_from_evaluation_and_identities_are_unique():
    report = audit_dataset(frames(), "fixture", replay_warmup_15m=80)
    assert report["timeframes"]["15m"]["warmup_rows"] == 80
    assert report["timeframes"]["15m"]["evaluation_rows"] == 20
    assert report["timeframes"]["15m"]["unique_rows"] == 100
    assert report["timeframes"]["15m"]["duplicates"] == 0


def test_daily_one_candle_per_symbol_session_validation():
    source = frames(); source["TEST"]["1d"].append(source["TEST"]["1d"][-1])
    report = daily_session_audit(source, BistMarketSession())
    assert report["more_than_one_candle_per_session"] == 1
    assert not report["valid"]


def test_hourly_provider_anchors_are_inferred_not_forced_to_session_open():
    day = datetime(2026, 1, 5, 6, 30, tzinfo=timezone.utc)  # 09:30 Istanbul
    source = {"TEST": {"1h": [candle(day + timedelta(hours=i)) for i in range(9)], "15m": [], "1d": []}}
    report = audit_dataset(source, "fixture", replay_warmup_15m=0)["hourly_session"]
    assert report["provider_anchors"][0] == "09:30"
    assert report["missing_bar_count"] == 0


def test_hourly_session_close_caps_provider_bar_close_time():
    session=BistMarketSession()
    bar=datetime(2026,1,5,14,30,tzinfo=timezone.utc)  # 17:30 Istanbul
    assert not session.candle_is_closed(bar,"1h",datetime(2026,1,5,14,59,tzinfo=timezone.utc))
    assert session.candle_is_closed(bar,"1h",datetime(2026,1,5,15,0,tzinfo=timezone.utc))
    closing_marker=datetime(2026,1,5,15,0,tzinfo=timezone.utc)
    assert session.candle_is_closed(closing_marker,"1h",closing_marker)


def test_oos_period_must_not_overlap_v3():
    with pytest.raises(ValueError, match="çakışamaz"):
        assert_oos_period(V3_RESEARCH_END - timedelta(days=1), V3_RESEARCH_END + timedelta(days=1))
    assert_oos_period(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 3, 1, tzinfo=timezone.utc))


def test_strategy_config_hash_is_deterministic_and_detects_change():
    base = AppSettings(strategy_version="v3")
    first = strategy_config_snapshot(base)
    assert first == strategy_config_snapshot(base)
    changed = strategy_config_snapshot(base.model_copy(update={"entry_score": 81}))
    assert first["sha256"] != changed["sha256"]


def test_exposure_fraction_is_formatted_once_as_percentage():
    assert percent_from_fraction([Decimal("0.0918")]) == 9.18
    assert percent_from_fraction([0, .2]) == 10.0


def test_risk_and_position_clamp_diagnostics_explain_final_quantity():
    result = position_size_diagnostics(Decimal("5000"), Decimal("5000"), Decimal("100"), Decimal("95"),
        Decimal(".005"), Decimal(".20"), Decimal(".10"))
    assert result["allowed_risk"] == 25
    assert result["risk_based_qty"] == 5
    assert result["max_position_qty"] == 10
    assert result["final_qty"] == 5
    assert result["risk_utilization_pct"] == 100
    assert result["binding_constraints"] == ["risk"]


def test_bootstrap_uncertainty_is_deterministic():
    first = bootstrap_summary([-1, 0, 1, 2])
    assert first == bootstrap_summary([-1, 0, 1, 2])
    assert first["n"] == 4 and first["positive_rate"] == 50


def test_eodhd_five_minute_aggregation_is_deterministic_and_read_only():
    start = datetime(2026, 1, 5, 7, tzinfo=timezone.utc)
    raw = [candle(start + timedelta(minutes=5 * i), 100 + i) for i in range(3)]
    before = list(raw); output = EodhdHistoricalProvider.aggregate_15m(raw)
    assert raw == before and len(output) == 1
    assert output[0].open == Decimal("100") and output[0].close == Decimal("102")
    assert output[0].volume == Decimal("3000")
