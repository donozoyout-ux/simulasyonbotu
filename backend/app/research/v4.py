from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from statistics import mean, median, pstdev

from app.analysis.pipeline import analyze_frames
from app.market_data.market_session import BistMarketSession
from app.strategy.scoring_engine import DEFAULT_WEIGHTS


V3_RESEARCH_START = datetime(2026, 6, 19, 6, 45, tzinfo=timezone.utc)
V3_RESEARCH_END = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
FROZEN_FIELDS = (
    "entry_score", "watchlist_score", "risk_per_trade_pct", "max_position_size_pct",
    "minimum_cash_reserve_pct", "max_open_positions", "min_rr", "commission_rate",
    "slippage_rate", "minimum_stop_atr_pct", "maximum_stop_atr_pct", "intrabar_policy",
    "bist_timezone", "bist_session_open", "bist_session_close", "strategy_version",
)


def _json_value(value):
    if isinstance(value, Decimal): return str(value)
    return value


def strategy_config_snapshot(config) -> dict:
    values = {key: _json_value(getattr(config, key)) for key in FROZEN_FIELDS}
    values["score_component_max"] = dict(DEFAULT_WEIGHTS)
    values["setup_rules_version"] = "v3-structural-quality"
    values["stop_generation"] = "setup-invalidation-with-atr-sanity"
    values["target_generation"] = "nearest-resistance-else-3atr"
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {"values": values, "sha256": hashlib.sha256(canonical).hexdigest()}


def assert_oos_period(start: datetime, end: datetime,
                      research_start: datetime = V3_RESEARCH_START,
                      research_end: datetime = V3_RESEARCH_END) -> None:
    if end < start: raise ValueError("OOS end start'tan önce olamaz")
    if start <= research_end and end >= research_start:
        raise ValueError("OOS period V3 research period ile çakışamaz")


def bootstrap_summary(values: list[float], samples: int = 500, seed: int = 42) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "positive_rate": None,
                "stddev": None, "standard_error": None, "ci95": [None, None]}
    numeric = [float(value) for value in values]
    rng = random.Random(seed)
    boot = sorted(mean(rng.choices(numeric, k=len(numeric))) for _ in range(samples))
    deviation = pstdev(numeric) if len(numeric) > 1 else 0.0
    return {"n": len(numeric), "mean": round(mean(numeric), 6), "median": round(median(numeric), 6),
            "positive_rate": round(sum(value > 0 for value in numeric) / len(numeric) * 100, 4),
            "stddev": round(deviation, 6), "standard_error": round(deviation / math.sqrt(len(numeric)), 6),
            "ci95": [round(boot[int(samples * .025)], 6), round(boot[min(samples - 1, int(samples * .975))], 6)]}


def _local_day(candle, session):
    return candle.timestamp.astimezone(session.tz).date()


def audit_dataset(frames: dict, source: str, replay_warmup_15m: int = 80,
                  session: BistMarketSession | None = None) -> dict:
    session = session or BistMarketSession()
    trigger_times = sorted({c.timestamp for symbol in frames.values() for c in symbol.get("15m", [])})
    evaluation_start = trigger_times[replay_warmup_15m] if len(trigger_times) > replay_warmup_15m else None
    evaluation_end = trigger_times[-1] if trigger_times else None
    result = {"source": source, "evaluation_start": evaluation_start.isoformat() if evaluation_start else None,
              "evaluation_end": evaluation_end.isoformat() if evaluation_end else None, "timeframes": {}}
    for timeframe in ("15m", "1h", "1d"):
        identity = []
        per_symbol = {}
        all_times = []
        for symbol, symbol_frames in sorted(frames.items()):
            rows = symbol_frames.get(timeframe, [])
            keys = [(symbol, timeframe, c.timestamp.astimezone(timezone.utc).isoformat()) for c in rows]
            identity.extend(keys); all_times.extend(c.timestamp for c in rows)
            warmup = sum(evaluation_start is not None and c.timestamp < evaluation_start for c in rows)
            evaluation = sum(evaluation_start is not None and evaluation_start <= c.timestamp <= evaluation_end for c in rows)
            per_symbol[symbol] = {"rows": len(rows), "warmup_rows": warmup, "evaluation_rows": evaluation,
                                  "duplicates": len(keys) - len(set(keys))}
        result["timeframes"][timeframe] = {
            "raw_start": min(all_times).isoformat() if all_times else None,
            "raw_end": max(all_times).isoformat() if all_times else None,
            "warmup_rows": sum(row["warmup_rows"] for row in per_symbol.values()),
            "evaluation_rows": sum(row["evaluation_rows"] for row in per_symbol.values()),
            "unique_rows": len(set(identity)), "duplicates": len(identity) - len(set(identity)),
            "symbols": len(per_symbol), "unique_timestamps": len(set(all_times)), "rows_per_symbol": per_symbol,
        }
    result["hourly_session"] = hourly_session_audit(frames, session, evaluation_start, evaluation_end)
    result["daily_session"] = daily_session_audit(frames, session, evaluation_start, evaluation_end)
    return result


def hourly_session_audit(frames: dict, session: BistMarketSession, start=None, end=None) -> dict:
    observed = Counter(); by_session = Counter()
    for symbol, symbol_frames in frames.items():
        for candle in symbol_frames.get("1h", []):
            if start and candle.timestamp < start: continue
            if end and candle.timestamp > end: continue
            local = candle.timestamp.astimezone(session.tz)
            observed[local.strftime("%H:%M")] += 1
            by_session[(symbol, local.date(), local.strftime("%H:%M"))] += 1
    anchors = sorted(observed, key=lambda value: time.fromisoformat(value))
    symbol_days = {(symbol, day) for symbol, day, _ in by_session}
    # Yahoo sometimes emits an isolated 18:00 closing marker. It is not a
    # regular hourly bar and must not become an expected anchor for every day.
    regular = [anchor for anchor in anchors if observed[anchor] >= len(symbol_days) * .8]
    optional = [anchor for anchor in anchors if anchor not in regular]
    boundary_day = start.astimezone(session.tz).date() if start else None
    full_symbol_days = {(symbol, day) for symbol, day in symbol_days if day != boundary_day}
    missing = sum(len(regular) - sum((symbol, day, anchor) in by_session for anchor in regular)
                  for symbol, day in full_symbol_days)
    return {"provider_anchors": anchors, "regular_expected_anchors": regular, "optional_anchors": optional,
            "expected_bars_per_full_symbol_day": len(regular),
            "observed_bar_count": sum(observed.values()), "observed_by_anchor": dict(observed),
            "full_symbol_days": len(full_symbol_days), "expected_bar_count": len(full_symbol_days) * len(regular),
            "missing_bar_count": missing,
            "note": "Regular anchors are inferred from >=80% provider prevalence. Partial boundary day and isolated closing markers are excluded from missing counts."}


def daily_session_audit(frames: dict, session: BistMarketSession, start=None, end=None) -> dict:
    identities = Counter()
    for symbol, symbol_frames in frames.items():
        for candle in symbol_frames.get("1d", []):
            if start and candle.timestamp < start: continue
            if end and candle.timestamp > end: continue
            identities[(symbol, _local_day(candle, session))] += 1
    duplicates = sum(max(0, count - 1) for count in identities.values())
    dates = {day for _, day in identities}; symbols = len(frames)
    return {"symbol_sessions": len(identities), "more_than_one_candle_per_session": duplicates,
            "expected_symbol_sessions": len(dates) * symbols,
            "missing_symbol_sessions": max(0, len(dates) * symbols - len(identities)), "valid": duplicates == 0}


def position_size_diagnostics(portfolio_value: Decimal, cash: Decimal, entry: Decimal, stop: Decimal,
                              risk_pct: Decimal, max_position_pct: Decimal, reserve_pct: Decimal) -> dict:
    allowed_risk = portfolio_value * risk_pct
    stop_distance = entry - stop
    risk_qty = int((allowed_risk / stop_distance).to_integral_value(rounding=ROUND_DOWN)) if stop_distance > 0 else 0
    cash_qty = int((cash / entry).to_integral_value(rounding=ROUND_DOWN)) if entry > 0 else 0
    max_position_qty = int((portfolio_value * max_position_pct / entry).to_integral_value(rounding=ROUND_DOWN)) if entry > 0 else 0
    reserve_qty = int((max(Decimal(0), cash - portfolio_value * reserve_pct) / entry).to_integral_value(rounding=ROUND_DOWN)) if entry > 0 else 0
    final_qty = min(risk_qty, cash_qty, max_position_qty, reserve_qty)
    actual_risk = max(Decimal(0), stop_distance) * final_qty
    constraints = {"risk": risk_qty, "cash": cash_qty, "max_position": max_position_qty, "cash_reserve": reserve_qty}
    return {"allowed_risk": float(allowed_risk), "actual_risk": float(actual_risk),
            "risk_utilization_pct": float(actual_risk / allowed_risk * 100) if allowed_risk else 0,
            "risk_based_qty": risk_qty, "cash_based_qty": cash_qty, "max_position_qty": max_position_qty,
            "reserve_based_qty": reserve_qty, "final_qty": final_qty,
            "binding_constraints": [key for key, value in constraints.items() if value == final_qty]}


def percent_from_fraction(values: list[float]) -> float:
    return float(round(mean(values) * 100, 4)) if values else 0.0


def _oos_performance(rows: list[dict]) -> dict:
    output = {"n": len(rows)}
    for horizon, label in ((4, "1h"), (16, "4h"), (32, "1d")):
        values = [row["forward_returns"][str(horizon)] for row in rows]
        output[label] = bootstrap_summary(values)
        output[label]["mfe"] = round(mean(row["excursions"][str(horizon)]["mfe"] for row in rows), 6) if rows else None
        output[label]["mae"] = round(mean(row["excursions"][str(horizon)]["mae"] for row in rows), 6) if rows else None
    return output


def distribution_percentiles(values: list[float]) -> dict:
    if not values: return {"p25": None, "median": None, "p75": None, "p90": None}
    ordered=sorted(float(value) for value in values)
    def pick(fraction): return ordered[round((len(ordered)-1)*fraction)]
    return {"p25":round(pick(.25),6),"median":round(pick(.5),6),"p75":round(pick(.75),6),"p90":round(pick(.9),6)}


def analyze_oos_records(records: list[dict], entry_score: int = 82, min_rr: float = 1.5) -> dict:
    def score_bucket(score):
        if score < 30: return "0-29"
        if score < 40: return "30-39"
        if score < 50: return "40-49"
        if score < 60: return "50-59"
        if score < 70: return "60-69"
        if score < 80: return "70-79"
        if score < 90: return "80-89"
        return "90-100"
    groups = {}
    for row in records: groups.setdefault(score_bucket(row["score"]), []).append(row)
    coarse_groups = {"<50": [], "50-59": [], "60-69": [], "70-79": [], "80+": []}
    for row in records:
        score=row["score"]
        key="<50" if score<50 else "50-59" if score<60 else "60-69" if score<70 else "70-79" if score<80 else "80+"
        coarse_groups[key].append(row)
    sorted_rows = sorted(records, key=lambda row: row["score"], reverse=True)
    cutoffs = {"top_5_pct": .05, "top_10_pct": .10, "top_20_pct": .20}
    percentile = {key: _oos_performance(sorted_rows[:max(1, round(len(records) * fraction))])
                  for key, fraction in cutoffs.items()}
    percentile["remaining_universe"] = _oos_performance(sorted_rows[max(1, round(len(records) * .20)):])
    setups = {name: _oos_performance([row for row in records if row["setup_detected"] and row["setup"] == name])
              for name in ("BREAKOUT", "PULLBACK", "SUPPORT_BOUNCE", "TREND_CONTINUATION")}
    setup_score = [row for row in records if row["setup_detected"] and row["score"] >= entry_score]
    cohorts = {"setup_score": _oos_performance(setup_score),
        "setup_score_rr": _oos_performance([row for row in setup_score if row["rr"] >= min_rr]),
        "setup_score_htf_rr": _oos_performance([row for row in setup_score if row["htf"] and row["rr"] >= min_rr]),
        "setup_score_htf": _oos_performance([row for row in setup_score if row["htf"]])}
    rvol_groups = {"<1": [], "1-1.5": [], "1.5-2": [], "2-3": [], "3+": []}
    for row in records:
        value = row["rvol"]; key = "<1" if value < 1 else "1-1.5" if value < 1.5 else "1.5-2" if value < 2 else "2-3" if value < 3 else "3+"
        rvol_groups[key].append(row)
    rr_rejections = [{key: row[key] for key in ("symbol", "timestamp", "setup", "score", "entry", "stop", "target", "rr", "forward_returns", "mfe", "mae")}
                     for row in setup_score if row["rr"] < min_rr]
    excursion_distributions={}
    for name in ("BREAKOUT","PULLBACK","SUPPORT_BOUNCE","TREND_CONTINUATION"):
        rows=[row for row in records if row["setup_detected"] and row["setup"]==name]
        excursion_distributions[name]={label:{"mfe":distribution_percentiles([row["excursions"][str(h)]["mfe"] for row in rows]),
            "mae":distribution_percentiles([row["excursions"][str(h)]["mae"] for row in rows])} for h,label in ((4,"1h"),(16,"4h"),(32,"1d"))}
    quality_groups={"<60":[],"60-69":[],"70-79":[],"80+":[]}
    for row in records:
        key="<60" if row["score"]<60 else "60-69" if row["score"]<70 else "70-79" if row["score"]<80 else "80+"
        quality_groups[key].append(row)
    entry_quality={key:{"n":len(rows),"mfe_4h":distribution_percentiles([row["excursions"]["16"]["mfe"] for row in rows]),
        "mae_4h":distribution_percentiles([row["excursions"]["16"]["mae"] for row in rows])} for key,rows in quality_groups.items()}
    support=[row for row in records if row["setup_detected"] and row["setup"]=="SUPPORT_BOUNCE"]
    support_horizons={str(h):bootstrap_summary([row["forward_returns"][str(h)] for row in support]) for h in (1,2,4,8,16,32)}
    breakout=[row for row in records if row["setup_detected"] and row["setup"]=="BREAKOUT"]
    breakout_quality={label:_oos_performance([row for row in breakout if predicate(row["breakdown"]["setup"]*10)]) for label,predicate in
        (("low",lambda value:value<40),("medium",lambda value:40<=value<70),("high",lambda value:value>=70))}
    return {"score_distribution": {**bootstrap_summary([row["score"] for row in records]),
                "buckets": dict(Counter(score_bucket(row["score"]) for row in records))},
            "score_vs_forward": {key: _oos_performance(rows) for key, rows in groups.items()},
            "score_vs_forward_coarse": {key: _oos_performance(rows) for key, rows in coarse_groups.items()},
            "percentile_validation": percentile, "setup_performance": setups, "filter_cohorts": cohorts,
            "rvol_performance": {key: _oos_performance(rows) for key, rows in rvol_groups.items()},
            "rr_rejected_candidates": rr_rejections,"excursion_distributions":excursion_distributions,
            "entry_quality_vs_excursion":entry_quality,"support_bounce_all_horizons":support_horizons,
            "breakout_quality":breakout_quality}


def frozen_pipeline_result(frames, config, *args, **kwargs):
    before = strategy_config_snapshot(config)["sha256"]
    result = analyze_frames(frames=frames, config=config, *args, **kwargs)
    if strategy_config_snapshot(config)["sha256"] != before:
        raise RuntimeError("Strategy configuration mutated during OOS evaluation")
    return result
