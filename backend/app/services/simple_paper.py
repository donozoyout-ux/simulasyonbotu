from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import uuid

from sqlalchemy import desc, select

from app.analysis.indicators import ema, rsi, sma
from app.market_data.market_session import BistMarketSession
from app.models import DecisionLog
from app.portfolio.simple_paper_broker import SimplePaperBroker
from app.scanner.bist_scanner import get_provider, safe_provider_error
from app.services.simple_state import get_simple_state
from app.services.telegram import TelegramNotifier

MODE = "SIMPLE_PAPER_V1"
SIMPLE_UNIVERSE = [
    "AGHOL", "AKSA", "ALBRK", "ALFAS", "ANHYT", "AYDEM", "AYGAZ", "BERA",
    "BRSAN", "CIMSA", "DOAS", "ECILC", "ENJSA", "GESAN", "GWIND", "ISDMR",
    "KCAER", "KLSER", "KONTR", "LOGO", "MAVI", "MPARK", "NTHOL", "ODAS",
    "OYAKC", "SOKM", "TABGD", "TKFEN",
]
logger = logging.getLogger("SIMPLE_PAPER")


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _number(value) -> float:
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
        return (f"15m {self.change_15m:+.2f}% | 1h {self.change_1h:+.2f}% | "
            f"EMA20 {'>' if self.ema20 > self.ema50 else '<='} EMA50 | "
            f"RSI {self.rsi14:.1f} | Volume {self.volume/self.volume_sma20:.2f}x")

    def payload(self, threshold: int, scan_id: str) -> dict:
        return {"scan_id": scan_id, "symbol": self.symbol, "price": _number(self.price),
            "change_15m_pct": _number(self.change_15m), "change_1h_pct": _number(self.change_1h),
            "ema20": _number(self.ema20), "ema50": _number(self.ema50), "rsi14": _number(self.rsi14),
            "volume": _number(self.volume), "volume_sma20": _number(self.volume_sma20),
            "volume_ratio": _number(self.volume/self.volume_sma20), "score": self.score,
            "decision": "BUY" if self.score >= threshold else "WATCH" if self.score >= 50 else "SKIP",
            "candle_time": self.candle_time.isoformat(), "reason": self.reason(),
            "score_breakdown": {"price_above_ema20": 25 if self.price > self.ema20 else 0,
                "ema20_above_ema50": 20 if self.ema20 > self.ema50 else 0,
                "positive_1h": 20 if self.change_1h > 0 else 0,
                "positive_15m": 15 if self.change_15m > 0 else 0,
                "rsi_50_70": 10 if Decimal(50) <= self.rsi14 <= Decimal(70) else 0,
                "volume_above_sma20": 10 if self.volume > self.volume_sma20 else 0}}


def analyze_symbol(symbol: str, candles) -> SimpleCandidate:
    closed = [row for row in candles if row.closed]
    if len(closed) < 50: raise ValueError(f"{symbol}: en az 50 kapalı 15m mum gerekli")
    closes, volumes = [row.close for row in closed], [row.volume for row in closed]
    if closes[-1] <= 0 or any(value <= 0 for value in closes[-50:]): raise ValueError(f"{symbol}: geçersiz fiyat")
    e20, e50, r14, v20 = ema(closes, 20), ema(closes, 50), rsi(closes, 14), sma(volumes, 20)
    if not all((e20, e50, r14, v20)) or v20[-1] <= 0: raise ValueError(f"{symbol}: gösterge üretilemedi")
    price = closes[-1]; change_15m = (price/closes[-2]-1)*100; change_1h = (price/closes[-5]-1)*100
    score = (25 if price > e20[-1] else 0) + (20 if e20[-1] > e50[-1] else 0)
    score += (20 if change_1h > 0 else 0) + (15 if change_15m > 0 else 0)
    score += (10 if Decimal(50) <= r14[-1] <= Decimal(70) else 0) + (10 if volumes[-1] > v20[-1] else 0)
    return SimpleCandidate(symbol, price, change_15m, change_1h, e20[-1], e50[-1], r14[-1],
                           volumes[-1], v20[-1], score, _utc(closed[-1].timestamp))


def _db_snapshot(db, store, category: str, decision: str, reason: str, *, symbol=None, score=None, details=None):
    if db is None:
        store.database("UNAVAILABLE"); return False
    state = store.read()
    if state.get("database_checked") and state.get("database_status") != "OK": return False
    try:
        db.add(DecisionLog(symbol=symbol, category=category, decision=decision, reason=reason, score=score,
            details=details or {}, strategy_version=MODE))
        db.add(DecisionLog(category="SIMPLE_STATE", decision="SNAPSHOT", reason="Simple runtime state",
            details=store.read(), strategy_version=MODE))
        db.commit(); store.database("OK"); return True
    except Exception as exc:
        try: db.rollback()
        except Exception: pass
        store.database("DEGRADED")
        logger.warning("simple_db_persistence_failed type=%s", type(exc).__name__)
        return False


def _safe_send(notifier, message: str):
    try: return notifier.send(message)
    except Exception as exc:
        logger.warning("simple_telegram_failed type=%s", type(exc).__name__)
        return {"status": "ERROR"}


def bootstrap_simple_state(db, config, notifier=None):
    store = get_simple_state(config); notifier = notifier or TelegramNotifier(config)
    if db is not None:
        try:
            db.execute(select(1))
            if store.cold_started:
                row = db.scalar(select(DecisionLog).where(DecisionLog.category == "SIMPLE_STATE",
                    DecisionLog.strategy_version == MODE).order_by(desc(DecisionLog.created_at)).limit(1))
                if row and isinstance(row.details, dict): store.replace_from_database(row.details)
                else: store.database("OK")
            else: store.database("OK")
            return store
        except Exception as exc:
            try: db.rollback()
            except Exception: pass
            logger.warning("simple_db_bootstrap_failed type=%s", type(exc).__name__)
    store.database("UNAVAILABLE")
    if store.cold_started and not store.read().get("restart_alert_sent"):
        store.mutate(lambda state: state.update(restart_alert_sent=True))
        _safe_send(notifier, "⚠️ <b>SIMPLE STATE RESTARTED</b>\nPersistent database unavailable.\n"
            "Simple paper state started flat.\nReal orders remain disabled.")
    return store


def _entry_gate_state(config, state: dict, candidate: dict | None, now: datetime,
                      market_open: bool, allow_entry: bool) -> tuple[bool, str | None]:
    if not market_open or not allow_entry: return False, "MARKET_CLOSED"
    if state.get("paused"): return False, "PAUSED"
    if state.get("open_position"): return False, "OPEN_POSITION_EXISTS"
    if not candidate or candidate.get("score", 0) < config.simple_entry_score: return False, "SCORE_BELOW_THRESHOLD"
    candle_time = _utc(datetime.fromisoformat(candidate["candle_time"]))
    if candle_time > now or now-candle_time > timedelta(minutes=30): return False, "STALE_DATA_BLOCK"
    if int(state.get("trades_today", 0)) >= config.simple_max_trades_per_day: return False, "DAILY_TRADE_LIMIT"
    loss_limit = Decimal(str(state["initial_cash"]))*config.simple_daily_loss_limit_pct
    if Decimal(str(state.get("daily_realized_pnl", 0))) <= -loss_limit: return False, "DAILY_LOSS_BRAKE"
    exited = state.get("last_exit_by_symbol", {}).get(candidate["symbol"])
    if exited and now-_utc(datetime.fromisoformat(exited)) < timedelta(minutes=config.simple_cooldown_minutes):
        return False, "COOLDOWN"
    return True, None


class SimplePaperEngine:
    def __init__(self, db, config, provider=None, notifier=None, store=None):
        self.db, self.config = db, config
        self.provider = provider or get_provider(config)
        self.notifier = notifier or TelegramNotifier(config)
        self.store = store or get_simple_state(config)
        self.broker = SimplePaperBroker(self.store, config.commission_rate, config.slippage_rate)

    def _entry_gate(self, candidate: dict | None, now: datetime, market_open: bool, allow_entry: bool) -> tuple[bool, str | None]:
        return _entry_gate_state(self.config, self.store.read(now), candidate, now, market_open, allow_entry)

    def _alert_gate_change(self, reason: str | None, now: datetime):
        state = self.store.read(now); previous = state.get("last_data_state")
        current = "STALE" if reason == "STALE_DATA_BLOCK" else "OK"
        if current != previous:
            self.store.mutate(lambda value: value.update(last_data_state=current), now)
            if current == "STALE": _safe_send(self.notifier, "⚠️ <b>STALE DATA BLOCK</b>\nSimple PAPER BUY disabled until fresh closed 15m data arrives.")
        if reason == "DAILY_LOSS_BRAKE" and state.get("loss_brake_alert_date") != state.get("daily_date"):
            limit = Decimal(str(state["initial_cash"]))*self.config.simple_daily_loss_limit_pct
            self.store.mutate(lambda value: value.update(loss_brake_alert_date=value.get("daily_date")), now)
            _safe_send(self.notifier, "🛑 <b>DAILY LOSS BRAKE</b>\n\n"
                f"Daily PnL: {state.get('daily_realized_pnl', 0):.2f} TL\nLimit: -{limit:.2f} TL\n"
                "New paper entries disabled until next trading day.")

    def scan(self, now=None, market_open: bool | None=None, allow_entry=True, max_symbols=None):
        now = _utc(now); market_open = BistMarketSession.from_config(self.config).is_open(now) if market_open is None else market_open
        limit = min(max_symbols or self.config.simple_symbol_limit, len(SIMPLE_UNIVERSE)); symbols = SIMPLE_UNIVERSE[:limit]
        candidates, failures = [], []; scan_id = f"SIMPLE-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        for symbol in symbols:
            try: candidates.append(analyze_symbol(symbol, self.provider.get_candles(symbol, "15m", 80)))
            except Exception as exc: failures.append({"symbol": symbol, "error": safe_provider_error(exc)})
        candidates.sort(key=lambda item: (-item.score, item.symbol))
        payloads = [item.payload(self.config.simple_entry_score, scan_id) for item in candidates]
        best = payloads[0] if payloads else None
        entry_candidate = best; allowed, block = self._entry_gate(best, now, market_open, allow_entry)
        if not allowed and block in {"COOLDOWN", "STALE_DATA_BLOCK"}:
            for item in payloads[1:]:
                candidate_allowed, candidate_block = self._entry_gate(item, now, market_open, allow_entry)
                if candidate_allowed:
                    entry_candidate, allowed, block = item, True, None
                    break
        summary = {"scan_id": scan_id, "requested": len(symbols), "valid_symbols": len(payloads),
            "failed_symbols": len(failures), "failures": failures, "candidates": payloads,
            "best_candidate": best, "market_open": market_open, "entries_enabled": allowed,
            "entry_block_reason": block, "entry_threshold": self.config.simple_entry_score,
            "minimum_valid_target": 10, "minimum_valid_met": len(payloads) >= 10, "real_orders": False,
            "scanned_at": now.isoformat()}
        previous = self.store.read(now).get("last_scan") or {}
        self.store.mutate(lambda state: state.update(last_scan=summary), now)
        previous_best = previous.get("best_candidate") or {}
        if best and (best["symbol"] != previous_best.get("symbol") or abs(best["score"]-previous_best.get("score", best["score"])) >= 10):
            _safe_send(self.notifier, self.notifier.simple_best_message(best, market_open, self.config.simple_entry_score))
        if not payloads:
            if self.store.read(now).get("last_data_state") != "SCAN_ERROR":
                self.store.mutate(lambda value: value.update(last_data_state="SCAN_ERROR"), now)
                _safe_send(self.notifier, "⚠️ <b>SIMPLE DATA ERROR</b>\nNo valid candidate was produced.\nNo paper entry was attempted.")
        else:
            self._alert_gate_change(block, now)
        entry = self._buy(entry_candidate, now, scan_id) if allowed and entry_candidate else None
        _db_snapshot(self.db, self.store, "SIMPLE_PAPER_SCAN", "COMPLETE_WITH_ERRORS" if failures else "COMPLETE",
            f"{len(payloads)} valid / {len(failures)} failed", details=summary)
        return {"status": "completed", "mode": MODE, "analysis_mode": "LIVE" if market_open else "ANALYSIS_ONLY",
                **{key:value for key,value in summary.items() if key != "candidates"}, "entry": entry}

    def _buy(self, candidate: dict, now: datetime, scan_id: str):
        price = Decimal(str(candidate["price"])); stop = price*Decimal("0.99"); target = price*Decimal("1.02")
        position = self.broker.buy(candidate["symbol"], price, candidate["score"], candidate["reason"], stop, target, now)
        details = {**candidate, **position, "scan_id": scan_id,
            "portfolio_value": simple_status(None, self.config, now, self.store)["portfolio_value"], "real_order": False}
        _safe_send(self.notifier, self.notifier.simple_buy_message(candidate["symbol"], position["quantity"],
            position["entry_price"], position["stop"], position["target"], candidate["score"], details["portfolio_value"]))
        _db_snapshot(self.db, self.store, "SIMPLE_PAPER_ENTRY", "BUY", f"BUY {candidate['symbol']}: {candidate['reason']}",
            symbol=candidate["symbol"], score=candidate["score"], details=details)
        return details

    def update_positions(self, now=None):
        now = _utc(now); position = self.store.read(now).get("open_position")
        if not position: return {"status": "updated", "updated": [], "closed": [], "failures": []}
        try:
            prices = [row for row in self.provider.get_candles(position["symbol"], "5m", 3) if row.closed]
            if not prices or prices[-1].close <= 0: raise ValueError("kapalı 5m latest price yok")
            latest = prices[-1].close; live = self.broker.mark(latest, now); reason = None
            if latest <= Decimal(str(position["stop"])): reason = "STOP LOSS"
            elif latest >= Decimal(str(position["target"])): reason = "TAKE PROFIT"
            else:
                rows = [row for row in self.provider.get_candles(position["symbol"], "15m", 3) if row.closed]
                if len(rows) >= 2 and rows[-1].close < rows[-1].open and rows[-2].close < rows[-2].open:
                    reason = "TWO CONSECUTIVE DOWN 15M CANDLES"
                elif now-_utc(datetime.fromisoformat(position["opened_at"])) >= timedelta(days=1): reason = "MAX HOLDING 1 DAY"
            if not reason:
                _db_snapshot(self.db, self.store, "SIMPLE_MARK", "UPDATE", "Live PnL updated",
                    symbol=position["symbol"], score=position["score"], details=live)
                return {"status": "updated", "updated": [live], "closed": [], "failures": []}
            trade = self.broker.sell(latest, reason, now)
            _safe_send(self.notifier, self.notifier.simple_sell_message(trade["symbol"], trade["exit_price"],
                trade["realized_pnl"], trade["return_pct"], reason))
            _db_snapshot(self.db, self.store, "SIMPLE_PAPER_EXIT", "SELL", reason,
                symbol=trade["symbol"], score=position["score"], details=trade)
            return {"status": "updated", "updated": [], "closed": [trade], "failures": []}
        except Exception as exc:
            error = safe_provider_error(exc); state = self.store.read(now)
            if state.get("last_data_state") != "ERROR":
                self.store.mutate(lambda value: value.update(last_data_state="ERROR"), now)
                _safe_send(self.notifier, f"⚠️ <b>SIMPLE DATA ERROR</b>\n{position['symbol']} price update unavailable.\nPaper position remains open.")
            return {"status": "updated", "updated": [], "closed": [],
                    "failures": [{"symbol": position["symbol"], "error": error}]}

    def daily_summary(self, now=None):
        now = _utc(now); state = self.store.read(now); day = state.get("daily_date")
        if state.get("daily_summary_sent_date") == day: return {"status": "ALREADY_SENT"}
        trades = state.get("daily_trades", []); equity = SimplePaperBroker.equity(state)
        wins = [item for item in trades if item["realized_pnl"] > 0]; losses = [item for item in trades if item["realized_pnl"] <= 0]
        best = max(trades, key=lambda item:item["realized_pnl"], default=None); worst = min(trades, key=lambda item:item["realized_pnl"], default=None)
        position = state.get("open_position")
        message = ("📊 <b>GÜNLÜK PAPER ÖZET</b>\n\n"
            f"Başlangıç: ₺{state['daily_start_equity']:.2f}\nBitiş: ₺{equity:.2f}\nGünlük K/Z: {state['daily_realized_pnl']:+.2f} TL\n\n"
            f"İşlem: {state['trades_today']}\nKazanç: {len(wins)}\nKayıp: {len(losses)}\n"
            f"En iyi işlem: {best['symbol']+' '+format(best['realized_pnl'], '+.2f') if best else '—'}\n"
            f"En kötü işlem: {worst['symbol']+' '+format(worst['realized_pnl'], '+.2f') if worst else '—'}\n"
            f"Açık pozisyon: {position['symbol'] if position else 'NONE'}\n\nMode: {MODE}\nReal orders: FALSE")
        self.store.mutate(lambda value: value.update(daily_summary_sent_date=day), now)
        result = _safe_send(self.notifier, message)
        _db_snapshot(self.db, self.store, "SIMPLE_DAILY_SUMMARY", "SENT", "Daily simple paper summary")
        return {"status": result.get("status", "ERROR"), "message": message}


def simple_candidates(db, config, limit=5, store=None):
    scan = (store or get_simple_state(config)).read().get("last_scan") or {}
    return (scan.get("candidates") or [])[:limit]


def simple_status(db, config, now=None, store=None):
    now = _utc(now); store = store or get_simple_state(config); state = store.read(now)
    position = state.get("open_position"); equity = SimplePaperBroker.equity(state); unrealized = Decimal(0)
    if position:
        basis = Decimal(str(position["entry_price"]))*position["quantity"]+Decimal(str(position["entry_fees"]))
        unrealized = Decimal(str(position["current_price"]))*position["quantity"]-basis
    scan = state.get("last_scan") or {}; best = scan.get("best_candidate"); market_open = BistMarketSession.from_config(config).is_open(now)
    allowed, block = _entry_gate_state(config, state, best, now, market_open, True)
    if not allowed and block in {"COOLDOWN", "STALE_DATA_BLOCK"}:
        for item in (scan.get("candidates") or [])[1:]:
            candidate_allowed, candidate_block = _entry_gate_state(config, state, item, now, market_open, True)
            if candidate_allowed: allowed, block = True, None; break
    loss_limit = Decimal(str(state["initial_cash"]))*config.simple_daily_loss_limit_pct
    return {"mode": MODE, "market_open": market_open,
        "portfolio_value": _number(equity), "cash": _number(state["cash"]), "equity": _number(equity),
        "unrealized_pnl": _number(unrealized), "realized_pnl": _number(state.get("realized_pnl", 0)),
        "open_position": position, "last_scan_at": scan.get("scanned_at"), "valid_symbols": scan.get("valid_symbols", 0),
        "failed_symbols": scan.get("failed_symbols", 0), "best_candidate": best,
        "entry_threshold": config.simple_entry_score, "real_orders": False, "paused": state.get("paused", False),
        "database_required": False, "database_status": state.get("database_status", "UNAVAILABLE"),
        "state_backend": state.get("state_backend", "MEMORY"), "cooldown_minutes": config.simple_cooldown_minutes,
        "max_trades_per_day": config.simple_max_trades_per_day, "trades_today": state.get("trades_today", 0),
        "daily_loss_limit_pct": _number(config.simple_daily_loss_limit_pct), "daily_loss_limit_tl": _number(loss_limit),
        "daily_realized_pnl": _number(state.get("daily_realized_pnl", 0)), "entry_allowed": allowed,
        "entry_block_reason": block}
