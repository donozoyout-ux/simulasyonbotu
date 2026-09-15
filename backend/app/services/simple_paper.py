from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
import threading
import uuid

from sqlalchemy import desc, func, select

from app.analysis.indicators import ema, rsi, sma
from app.market_data.market_session import BistMarketSession
from app.models import DecisionLog, Position, Trade
from app.portfolio.paper_broker import PaperBroker
from app.portfolio.portfolio_manager import ensure_portfolio, portfolio_summary, take_snapshot
from app.scanner.bist_scanner import get_provider, safe_provider_error
from app.services.forward_test import ensure_forward_run
from app.services.telegram import TelegramNotifier

MODE = "SIMPLE_PAPER_V1"
SIMPLE_UNIVERSE = [
    "AGHOL", "AKSA", "ALBRK", "ALFAS", "ANHYT", "AYDEM", "AYGAZ", "BERA",
    "BRSAN", "CIMSA", "DOAS", "ECILC", "ENJSA", "GESAN", "GWIND", "ISDMR",
    "KCAER", "KLSER", "KONTR", "LOGO", "MAVI", "MPARK", "NTHOL", "ODAS",
    "OYAKC", "SOKM", "TABGD", "TKFEN",
]
_LOCK = threading.Lock()


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _number(value: Decimal) -> float:
    return round(float(value), 6)


@dataclass(frozen=True)
class SimpleCandidate:
    symbol: str
    price: Decimal
    change_15m: Decimal
    change_1h: Decimal
    ema20: Decimal
    ema50: Decimal
    rsi14: Decimal
    volume: Decimal
    volume_sma20: Decimal
    score: int
    candle_time: datetime

    def reason(self) -> str:
        return (
            f"15m {self.change_15m:+.2f}% | 1h {self.change_1h:+.2f}% | "
            f"EMA20 {'>' if self.ema20 > self.ema50 else '<='} EMA50 | "
            f"RSI {self.rsi14:.1f} | Volume {self.volume / self.volume_sma20:.2f}x"
        )

    def payload(self, threshold: int, scan_id: str) -> dict:
        return {
            "scan_id": scan_id, "symbol": self.symbol, "price": _number(self.price),
            "change_15m_pct": _number(self.change_15m), "change_1h_pct": _number(self.change_1h),
            "ema20": _number(self.ema20), "ema50": _number(self.ema50), "rsi14": _number(self.rsi14),
            "volume": _number(self.volume), "volume_sma20": _number(self.volume_sma20),
            "volume_ratio": _number(self.volume / self.volume_sma20), "score": self.score,
            "decision": "BUY" if self.score >= threshold else "WATCH" if self.score >= 50 else "SKIP",
            "candle_time": self.candle_time.isoformat(), "reason": self.reason(),
            "score_breakdown": {"price_above_ema20": 25 if self.price > self.ema20 else 0,
                "ema20_above_ema50": 20 if self.ema20 > self.ema50 else 0,
                "positive_1h": 20 if self.change_1h > 0 else 0,
                "positive_15m": 15 if self.change_15m > 0 else 0,
                "rsi_50_70": 10 if Decimal(50) <= self.rsi14 <= Decimal(70) else 0,
                "volume_above_sma20": 10 if self.volume > self.volume_sma20 else 0},
        }


def analyze_symbol(symbol: str, candles) -> SimpleCandidate:
    closed = [row for row in candles if row.closed]
    if len(closed) < 50:
        raise ValueError(f"{symbol}: en az 50 kapalı 15m mum gerekli; {len(closed)} geldi")
    closes = [row.close for row in closed]
    volumes = [row.volume for row in closed]
    if closes[-1] <= 0 or any(value <= 0 for value in closes[-50:]):
        raise ValueError(f"{symbol}: geçersiz fiyat")
    e20, e50, r14, v20 = ema(closes, 20), ema(closes, 50), rsi(closes, 14), sma(volumes, 20)
    if not all((e20, e50, r14, v20)) or v20[-1] <= 0:
        raise ValueError(f"{symbol}: gösterge üretilemedi")
    price = closes[-1]
    change_15m = (price / closes[-2] - 1) * 100
    change_1h = (price / closes[-5] - 1) * 100
    score = 0
    score += 25 if price > e20[-1] else 0
    score += 20 if e20[-1] > e50[-1] else 0
    score += 20 if change_1h > 0 else 0
    score += 15 if change_15m > 0 else 0
    score += 10 if Decimal(50) <= r14[-1] <= Decimal(70) else 0
    score += 10 if volumes[-1] > v20[-1] else 0
    return SimpleCandidate(symbol, price, change_15m, change_1h, e20[-1], e50[-1], r14[-1],
                           volumes[-1], v20[-1], score, _utc(closed[-1].timestamp))


class SimplePaperEngine:
    def __init__(self, db, config, provider=None, notifier=None):
        self.db, self.config = db, config
        self.provider = provider or get_provider(config)
        self.notifier = notifier or TelegramNotifier(config)

    def _run(self, now=None):
        return ensure_forward_run(self.db, self.config, _utc(now))

    def _simple_positions(self):
        return list(self.db.scalars(select(Position).where(
            Position.status == "OPEN", Position.strategy_version == MODE)).all())

    def _log(self, run, category, decision, reason, *, symbol=None, score=None, details=None, now=None):
        row = DecisionLog(symbol=symbol, category=category, decision=decision, reason=reason, score=score,
            details=details or {}, created_at=_utc(now), run_id=run.run_id, strategy_version=MODE,
            strategy_config_hash=run.strategy_config_hash)
        self.db.add(row)
        return row

    def scan(self, now=None, market_open: bool | None = None, allow_entry=True, max_symbols=None):
        now = _utc(now); run = self._run(now)
        market_open = BistMarketSession.from_config(self.config).is_open(now) if market_open is None else market_open
        limit = min(max_symbols or self.config.simple_symbol_limit, len(SIMPLE_UNIVERSE))
        symbols = SIMPLE_UNIVERSE[:limit]; candidates, failures = [], []
        scan_id = f"SIMPLE-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        with _LOCK:
            for symbol in symbols:
                try:
                    candidate = analyze_symbol(symbol, self.provider.get_candles(symbol, "15m", 80))
                    if market_open and (candidate.candle_time > now or now - candidate.candle_time > timedelta(minutes=45)):
                        raise ValueError(f"{symbol}: latest closed 15m candle stale")
                    candidates.append(candidate)
                except Exception as exc:
                    failures.append({"symbol": symbol, "error": safe_provider_error(exc)})
                    self.db.rollback()
            candidates.sort(key=lambda item: (-item.score, item.symbol))
            for item in candidates:
                payload = item.payload(self.config.simple_entry_score, scan_id)
                self._log(run, "SIMPLE_CANDIDATE", payload["decision"], item.reason(), symbol=item.symbol,
                          score=item.score, details=payload, now=now)
            best = candidates[0] if candidates else None
            summary = {"scan_id": scan_id, "requested": len(symbols), "valid_symbols": len(candidates),
                "failed_symbols": len(failures), "failures": failures,
                "best_candidate": best.payload(self.config.simple_entry_score, scan_id) if best else None,
                "market_open": market_open, "entries_enabled": bool(market_open and allow_entry and not run.paused),
                "entry_threshold": self.config.simple_entry_score, "minimum_valid_target": 10,
                "minimum_valid_met": len(candidates) >= 10, "real_orders": False}
            self._log(run, "SIMPLE_PAPER_SCAN", "COMPLETE_WITH_ERRORS" if failures else "COMPLETE",
                      f"{len(candidates)} valid / {len(failures)} failed", details=summary, now=now)
            self.db.commit()
            entry = None
            any_open = self.db.scalar(select(Position.id).where(Position.status == "OPEN").limit(1))
            if (summary["entries_enabled"] and best and best.score >= self.config.simple_entry_score
                    and not any_open and self.config.simple_max_open_positions >= 1):
                entry = self._buy(run, best, now, scan_id)
            return {"status": "completed", "mode": MODE, "analysis_mode": "LIVE" if market_open else "ANALYSIS_ONLY",
                    **summary, "entry": entry}

    def _buy(self, run, candidate: SimpleCandidate, now: datetime, scan_id: str):
        portfolio = ensure_portfolio(self.db, self.config.initial_balance)
        summary = portfolio_summary(self.db, self.config.initial_balance)
        cap = min(summary["portfolio_value"] * Decimal("0.20"), summary["cash_balance"])
        quantity = int((cap / candidate.price).to_integral_value(rounding=ROUND_DOWN))
        if quantity < 1:
            return None
        stop = candidate.price * Decimal("0.99"); target = candidate.price * Decimal("1.02")
        reason = candidate.reason()
        broker = PaperBroker(self.db, self.config.commission_rate, self.config.slippage_rate,
            run_id=run.run_id, strategy_version=MODE, strategy_config_hash=run.strategy_config_hash)
        position = broker.buy(portfolio, candidate.symbol, quantity, candidate.price, stop, target,
            MODE, candidate.score, reason, f"{MODE}:BUY:{candidate.symbol}:{candidate.candle_time.isoformat()}",
            candidate.candle_time, now)
        details = {**candidate.payload(self.config.simple_entry_score, scan_id), "quantity": quantity,
            "requested_entry_price": _number(candidate.price), "entry_price": _number(position.entry_price),
            "entry_time": now.isoformat(), "stop": _number(position.stop_price), "target": _number(position.target_price),
            "portfolio_value": _number(summary["portfolio_value"]), "real_order": False}
        self._log(run, "SIMPLE_PAPER_ENTRY", "BUY", f"BUY {candidate.symbol}: {reason}", symbol=candidate.symbol,
                  score=candidate.score, details=details, now=now)
        self.db.commit(); take_snapshot(self.db, self.config.initial_balance, run.run_id)
        self.notifier.send(self.notifier.simple_buy_message(candidate.symbol, quantity, position.entry_price,
            position.stop_price, position.target_price, candidate.score, summary["portfolio_value"]))
        return details

    def update_positions(self, now=None):
        now = _utc(now); run = self._run(now); updated, closed, failures = [], [], []
        portfolio = ensure_portfolio(self.db, self.config.initial_balance)
        for position in self._simple_positions():
            try:
                prices = [row for row in self.provider.get_candles(position.symbol, "5m", 3) if row.closed]
                if not prices: raise ValueError("kapalı 5m latest price yok")
                latest = prices[-1].close
                if latest <= 0: raise ValueError("latest price geçersiz")
                position.current_price = latest; self.db.commit()
                reason = None
                if latest <= position.stop_price: reason = "STOP LOSS"
                elif latest >= position.target_price: reason = "TAKE PROFIT"
                else:
                    rows = [row for row in self.provider.get_candles(position.symbol, "15m", 3) if row.closed]
                    if len(rows) >= 2 and rows[-1].close < rows[-1].open and rows[-2].close < rows[-2].open:
                        reason = "TWO CONSECUTIVE DOWN 15M CANDLES"
                    elif now - _utc(position.opened_at) >= timedelta(days=1): reason = "MAX HOLDING 1 DAY"
                if reason:
                    trade = PaperBroker(self.db, self.config.commission_rate, self.config.slippage_rate,
                        run_id=run.run_id, strategy_version=MODE, strategy_config_hash=run.strategy_config_hash).sell(
                            portfolio, position, position.quantity, latest, reason, execution_time=now)
                    details = {"symbol": trade.symbol, "quantity": trade.quantity, "exit_price": _number(trade.exit_price),
                        "exit_time": now.isoformat(), "realized_pnl": _number(trade.realized_pnl),
                        "return_pct": _number(trade.return_pct), "commission": _number(trade.commission),
                        "slippage": _number(trade.slippage_cost), "reason": reason,
                        "cash": _number(portfolio.cash_balance), "real_order": False}
                    self._log(run, "SIMPLE_PAPER_EXIT", "SELL", reason, symbol=trade.symbol,
                              score=trade.signal_score, details=details, now=now)
                    self.db.commit(); take_snapshot(self.db, self.config.initial_balance, run.run_id)
                    self.notifier.send(self.notifier.simple_sell_message(trade.symbol, trade.exit_price,
                        trade.realized_pnl, trade.return_pct, reason))
                    closed.append(details)
                else:
                    unrealized = latest * position.quantity - (position.entry_price * position.quantity + position.entry_fees)
                    updated.append({"symbol": position.symbol, "price": _number(latest),
                        "current_value": _number(latest * position.quantity), "unrealized_pnl": _number(unrealized),
                        "unrealized_pnl_pct": _number(unrealized / (position.entry_price * position.quantity) * 100)})
            except Exception as exc:
                self.db.rollback(); failures.append({"symbol": position.symbol, "error": safe_provider_error(exc)})
        return {"status": "updated", "updated": updated, "closed": closed, "failures": failures}


def simple_candidates(db, config, limit=5):
    run = ensure_forward_run(db, config)
    scan = db.scalar(select(DecisionLog).where(DecisionLog.run_id == run.run_id,
        DecisionLog.category == "SIMPLE_PAPER_SCAN").order_by(desc(DecisionLog.created_at)).limit(1))
    if not scan: return []
    scan_id = (scan.details or {}).get("scan_id")
    rows = db.scalars(select(DecisionLog).where(DecisionLog.run_id == run.run_id,
        DecisionLog.category == "SIMPLE_CANDIDATE").order_by(desc(DecisionLog.created_at)).limit(500)).all()
    result = [row.details for row in rows if (row.details or {}).get("scan_id") == scan_id]
    return sorted(result, key=lambda item: (-item.get("score", 0), item.get("symbol", "")))[:limit]


def simple_status(db, config, now=None):
    now = _utc(now); run = ensure_forward_run(db, config, now); summary = portfolio_summary(db, config.initial_balance)
    scan = db.scalar(select(DecisionLog).where(DecisionLog.run_id == run.run_id,
        DecisionLog.category == "SIMPLE_PAPER_SCAN").order_by(desc(DecisionLog.created_at)).limit(1))
    position = db.scalar(select(Position).where(Position.status == "OPEN", Position.strategy_version == MODE)
                         .order_by(desc(Position.opened_at)).limit(1))
    trade_pnl = db.scalar(select(func.coalesce(func.sum(Trade.realized_pnl), 0)).where(
        Trade.strategy_version == MODE, Trade.run_id == run.run_id))
    details = scan.details or {} if scan else {}
    open_payload = None if not position else {"symbol": position.symbol, "quantity": position.quantity,
        "entry_price": _number(position.entry_price), "current_price": _number(position.current_price),
        "stop": _number(position.stop_price), "target": _number(position.target_price),
        "unrealized_pnl": _number(position.current_price * position.quantity -
                                   (position.entry_price * position.quantity + position.entry_fees))}
    return {"mode": MODE, "market_open": BistMarketSession.from_config(config).is_open(now),
        "portfolio_value": _number(summary["portfolio_value"]), "cash": _number(summary["cash_balance"]),
        "equity": _number(summary["portfolio_value"]), "unrealized_pnl": _number(summary["unrealized_pnl"]),
        "realized_pnl": _number(Decimal(trade_pnl or 0)), "open_position": open_payload,
        "last_scan_at": scan.created_at.isoformat() if scan else None,
        "valid_symbols": details.get("valid_symbols", 0), "failed_symbols": details.get("failed_symbols", 0),
        "best_candidate": details.get("best_candidate"), "entry_threshold": config.simple_entry_score,
        "real_orders": False, "paused": run.paused}
