from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import escape
import json
import logging
import threading
from time import monotonic
from typing import Callable

import httpx
from sqlalchemy import desc, func, select

from app.ai.groq_advisor import ai_health
from app.db.session import SessionLocal
from app.market_data.market_session import BistMarketSession
from app.market_memory.service import MarketMemoryService
from app.models import DecisionLog, NewsItem, Position, ScanRun, Trade, WatchlistItem
from app.news.service import NewsService
from app.portfolio.portfolio_manager import portfolio_summary
from app.services.forward_test import active_forward_run, ensure_forward_run, set_paused
from app.services.system_health import classify_data_health
from app.services.telegram import TelegramNotifier


logger = logging.getLogger("TELEGRAM_COMMANDS")
MAX_MESSAGE = 3900
COMMANDS = [
    ("start", "Botu başlat / yardım"), ("status", "Sistem durumu"),
    ("portfolio", "Portföy"), ("positions", "Açık pozisyonlar"),
    ("watchlist", "Takip listesi"), ("scanner", "Son tarama"),
    ("health", "Sistem sağlığı"), ("news", "Son haberler"),
    ("alerts", "Bildirim ayarları"), ("pause", "Paper trading durdur"),
    ("resume", "Paper trading devam"), ("ping", "Bot online mı"),
    ("help", "Komutlar"),
]
_runtime_lock = threading.Lock()
_runtime = {"command_poller_running": False, "last_command_error": None}


def telegram_command_runtime_status() -> dict:
    with _runtime_lock:
        return {"command_poller_running": _runtime["command_poller_running"]}


def _set_runtime(running: bool, error: str | None = None):
    with _runtime_lock:
        _runtime["command_poller_running"] = running
        _runtime["last_command_error"] = error


def _money(value) -> str:
    return f"₺{Decimal(str(value or 0)):,.2f}"


def _bool(value: bool) -> str:
    return "ON" if value else "OFF"


class TelegramCommandService:
    """DB-only command dispatcher. It never calls a market-data provider."""

    def __init__(self, db, config):
        self.db, self.config = db, config

    def _run(self):
        return active_forward_run(self.db) or ensure_forward_run(self.db, self.config)

    def _last_scan(self, run):
        return self.db.scalar(select(ScanRun).where(ScanRun.run_id == run.run_id)
            .order_by(desc(ScanRun.started_at)).limit(1))

    def _help(self):
        return "🤖 <b>BIST BOT KOMUTLARI</b>\n\n" + "\n".join(
            f"/{name} — {escape(description)}" for name, description in COMMANDS)

    def _ping(self):
        session = BistMarketSession.from_config(self.config)
        now = datetime.now(timezone.utc)
        return ("🟢 <b>BIST BOT ONLINE</b>\n"
            f"Mode: {escape(self.config.operation_mode)}\n"
            f"Market: {'OPEN' if session.is_open(now) else 'CLOSED'}\n"
            f"Time: {now.astimezone(session.tz).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    def _status(self):
        run = self._run(); scan = self._last_scan(run)
        session = BistMarketSession.from_config(self.config); market_open = session.is_open()
        data = classify_data_health(scan, market_open)
        portfolio = portfolio_summary(self.db, self.config.initial_balance)
        funnel = (scan.funnel or {}) if scan else {}
        scan_text = "Henüz scan yok" if not scan else (
            f"{scan.total_symbols} requested\n{scan.valid_symbols} valid\n{scan.failed_symbols} failed\n"
            f"Highest score: {funnel.get('score_highest', 0)}\n"
            f"70+: {funnel.get('score_above_watchlist', 0)}\n82+: {funnel.get('score_above_entry', 0)}")
        return ("🤖 <b>BIST BOT STATUS</b>\n\n"
            f"Run: {escape(run.run_id)}\nStatus: {'PAUSED' if run.paused else 'RUNNING'}\n"
            f"Strategy: {escape(run.strategy_version)}\n\n"
            f"Market: {'OPEN' if market_open else 'CLOSED'}\n"
            f"Analysis: {escape(scan.analysis_mode if scan else ('LIVE' if market_open else 'ANALYSIS_ONLY'))}\n\n"
            f"Data: {escape(data['status'])}\nProvider: {escape(self.config.market_data_provider)}\n\n"
            f"Portfolio: {_money(portfolio['portfolio_value'])}\nOpen positions: {portfolio['open_positions']}\n\n"
            f"Last scan:\n{scan_text}\n\nAI execution: FALSE\nReal orders: FALSE")

    def _portfolio(self):
        run = self._run(); summary = portfolio_summary(self.db, self.config.initial_balance)
        trades = self.db.scalar(select(func.count()).select_from(Trade).where(Trade.run_id == run.run_id)) or 0
        return ("💼 <b>PAPER PORTFOLIO</b>\n\n"
            f"Initial: {_money(summary['initial_balance'])}\nCash: {_money(summary['cash_balance'])}\n"
            f"Portfolio value: {_money(summary['portfolio_value'])}\nRealized PnL: {_money(summary['realized_pnl'])}\n"
            f"Unrealized PnL: {_money(summary['unrealized_pnl'])}\n"
            f"Open positions: {summary['open_positions']}\nClosed trades: {trades}")

    def _positions(self):
        run = self._run()
        rows = self.db.scalars(select(Position).where(Position.run_id == run.run_id, Position.status == "OPEN")
            .order_by(desc(Position.opened_at)).limit(10)).all()
        if not rows: return "📭 Açık paper pozisyon yok."
        blocks = ["📌 <b>AÇIK PAPER POZİSYONLAR</b>"]
        for row in rows:
            pnl = row.current_price * row.quantity - (row.entry_price * row.quantity + row.entry_fees)
            blocks.append(f"\n<b>{escape(row.symbol)}</b>\nLot: {row.quantity}\nEntry: {row.entry_price}\n"
                f"Current: {row.current_price}\nStop: {row.stop_price}\nTarget: {row.target_price}\n"
                f"Unrealized PnL: {pnl}\nScore: {row.signal_score}")
        return "\n".join(blocks)

    def _watchlist(self):
        run = self._run()
        rows = self.db.scalars(select(WatchlistItem).where(WatchlistItem.run_id == run.run_id)
            .order_by(desc(WatchlistItem.score)).limit(10)).all()
        body = "\n".join(f"{index}. {escape(row.symbol)} — {row.score}" for index, row in enumerate(rows, 1))
        return ("👀 <b>WATCHLIST</b>\n\n" + (body or "Aktif watchlist kaydı yok.") +
            f"\n\nWatchlist threshold: {self.config.watchlist_score}\nEntry threshold: {self.config.entry_score}")

    def _scanner(self):
        run = self._run(); row = self._last_scan(run)
        if not row: return "🔎 Henüz tamamlanmış scan yok."
        funnel = row.funnel or {}; error_lines = []
        for item in (row.errors or [])[:5]:
            error_lines.append(f"{escape(str(item.get('symbol', 'UNKNOWN')))} — {escape(str(item.get('type') or item.get('error') or 'PROVIDER_ERROR'))}")
        errors = "\n".join(error_lines) or "Yok"
        return ("🔎 <b>SON SCAN</b>\n\n"
            f"Time: {row.started_at}\nAnalysis: {escape(row.analysis_mode)}\nRequested: {row.total_symbols}\n"
            f"Valid: {row.valid_symbols}\nFailed: {row.failed_symbols}\n"
            f"Quarantined: {funnel.get('quarantined_skipped', 0)}\n"
            f"Highest: {funnel.get('score_highest', 0)}\nAverage: {funnel.get('score_average', 0)}\n"
            f"70+: {funnel.get('score_above_watchlist', 0)}\n82+: {funnel.get('score_above_entry', 0)}\n"
            f"Signals: {row.signals}\nPaper buys: {row.entries}\n\nTop errors:\n{errors}")

    def _health(self):
        self.db.execute(select(1)); run = self._run(); scan = self._last_scan(run)
        market_open = BistMarketSession.from_config(self.config).is_open()
        data = classify_data_health(scan, market_open)
        news = NewsService(self.db, self.config).health()
        memory = MarketMemoryService(self.db, self.config).health()
        telegram = TelegramNotifier(self.config).status(); ai = ai_health(self.config)
        return ("🩺 <b>SYSTEM HEALTH</b>\n\nBackend: OK\nDB: OK\n"
            f"Provider: {escape(self.config.market_data_provider)}\nData health: {escape(data['system_status'])}\n"
            f"AI: {'OK' if ai.get('enabled') and ai.get('configured') else 'DISABLED/UNCONFIGURED'}\n"
            f"Telegram: {'OK' if telegram['configured'] else 'UNCONFIGURED'}\n"
            f"News: {escape(str(news.get('status', 'NO_DATA')))}\n"
            f"Market Memory: {escape(str(memory.get('status', 'NO_DATA')))}")

    def _news(self):
        rows = self.db.scalars(select(NewsItem).where(NewsItem.symbol.is_not(None))
            .order_by(desc(NewsItem.published_at)).limit(5)).all()
        if not rows: return "📰 Linked haber bulunamadı."
        body = []
        for row in rows:
            title = escape((row.title or "")[:180])
            body.append(f"\n<b>{escape(row.symbol or 'BIST')} • {escape(row.source)}</b>\n{title}\n"
                f"{escape(row.ai_sentiment or 'PENDING')} • önem {row.ai_importance if row.ai_importance is not None else '—'}")
        return "📰 <b>SON LINKED HABERLER</b>\n" + "\n".join(body)

    def _alerts(self):
        return ("🔔 <b>ALERT SETTINGS</b>\n\n"
            f"Signal alerts: {_bool(self.config.telegram_signal_alerts)}\nBuy/Sell alerts: ON\n"
            f"News min importance: {self.config.news_telegram_min_importance}\n"
            f"Data health alerts: {_bool(self.config.telegram_data_health_alerts)}\n"
            f"Off-hours signal alerts: {_bool(self.config.telegram_off_hours_analysis)}\n"
            f"Commands: {_bool(self.config.telegram_commands_enabled)}")

    def dispatch(self, command: str) -> str:
        handlers = {"start": self._help, "help": self._help, "ping": self._ping,
            "status": self._status, "portfolio": self._portfolio, "positions": self._positions,
            "watchlist": self._watchlist, "scanner": self._scanner, "health": self._health,
            "news": self._news, "alerts": self._alerts}
        if command == "pause":
            run = set_paused(self.db, self.config, True, create_if_missing=False)
            if run is None:
                return "⚠️ Aktif paper trading run bulunamadı. Yeni run oluşturulmadı."
            return f"⏸ <b>PAPER TRADING PAUSED</b>\n\nRun: {escape(run.run_id)}\nCollectors remain active."
        if command == "resume":
            run = set_paused(self.db, self.config, False, create_if_missing=False)
            if run is None:
                return "⚠️ Aktif paper trading run bulunamadı. Yeni run oluşturulmadı."
            return f"▶️ <b>PAPER TRADING RESUMED</b>\n\nRun: {escape(run.run_id)}"
        handler = handlers.get(command)
        return handler() if handler else "Bilinmeyen komut. Komut listesi için /help yazın."

    def handle_update(self, update: dict) -> str | None:
        message = update.get("message") or {}; text = message.get("text")
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != str(self.config.telegram_chat_id):
            return None
        if not isinstance(text, str) or not text.startswith("/"):
            return None
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            return None
        command = text.split()[0][1:].split("@", 1)[0].lower()
        reason = f"UPDATE:{update_id}"
        if self.db.scalar(select(DecisionLog.id).where(
            DecisionLog.category == "TELEGRAM_COMMAND", DecisionLog.reason == reason).limit(1)):
            return None
        active = active_forward_run(self.db)
        self.db.add(DecisionLog(category="TELEGRAM_COMMAND", decision=command[:32].upper(), reason=reason,
            details={"authorized": True}, run_id=active.run_id if active else None))
        try:
            response = self.dispatch(command)[:MAX_MESSAGE]
            self.db.commit()
            return response
        except Exception as exc:
            self.db.rollback()
            logger.warning("telegram_command_failed command=%s type=%s", command, type(exc).__name__)
            return "⚠️ Komut geçici olarak işlenemedi."


class TelegramCommandPoller:
    def __init__(self, config, session_factory: Callable = SessionLocal, client=None):
        self.config, self.session_factory = config, session_factory
        self.client = client
        self._owns_client = client is None
        self._stop = threading.Event(); self._thread = None; self._offset = None

    @property
    def configured(self):
        return bool(self.config.telegram_enabled and self.config.telegram_commands_enabled and
            self.config.telegram_bot_token and self.config.telegram_chat_id)

    def _url(self, method: str):
        token = self.config.telegram_bot_token.get_secret_value()
        return f"https://api.telegram.org/bot{token}/{method}"

    def _post(self, method: str, payload: dict):
        response = self.client.post(self._url(method), json=payload)
        response.raise_for_status()
        return response.json()

    def register_commands(self):
        try:
            self._post("setMyCommands", {"commands": [{"command": name, "description": description}
                for name, description in COMMANDS]})
        except Exception as exc:
            logger.warning("telegram_set_commands_failed type=%s", type(exc).__name__)

    def send_startup_alert(self):
        return TelegramNotifier(self.config, self.client).send("🟢 <b>BIST BOT ONLINE</b>\n\n"
            f"Mode: {escape(self.config.operation_mode)}\nStrategy: {escape(self.config.live_strategy_version)}\n"
            f"Provider: {escape(self.config.market_data_provider)}\nReal orders: FALSE")

    def poll_once(self):
        try:
            params = {"timeout": 3, "allowed_updates": json.dumps(["message"])}
            if self._offset is not None: params["offset"] = self._offset
            response = self.client.get(self._url("getUpdates"), params=params, timeout=5)
            response.raise_for_status(); body = response.json()
            for update in body.get("result", []) if body.get("ok") else []:
                update_id = update.get("update_id")
                if isinstance(update_id, int): self._offset = max(self._offset or 0, update_id + 1)
                with self.session_factory() as db:
                    reply = TelegramCommandService(db, self.config).handle_update(update)
                if reply: TelegramNotifier(self.config, self.client).send(reply)
            _set_runtime(True)
            return {"status": "OK", "updates": len(body.get("result", []))}
        except Exception as exc:
            _set_runtime(True, type(exc).__name__)
            logger.warning("telegram_poll_failed type=%s", type(exc).__name__)
            return {"status": "ERROR", "error": type(exc).__name__}

    def _loop(self):
        while not self._stop.is_set():
            started = monotonic()
            self.poll_once()
            cadence = max(10, int(self.config.telegram_command_poll_seconds))
            self._stop.wait(max(1, cadence - (monotonic() - started)))

    def start(self):
        if not self.configured or self._thread and self._thread.is_alive(): return
        try:
            if self.client is None: self.client = httpx.Client(timeout=self.config.telegram_timeout_seconds)
            self.register_commands()
            if self.config.telegram_startup_alert:
                self.send_startup_alert()
            self._stop.clear(); self._thread = threading.Thread(target=self._loop,
                name="telegram-command-poller", daemon=True); self._thread.start(); _set_runtime(True)
            logger.info("telegram_command_poller_started")
        except Exception as exc:
            _set_runtime(False, type(exc).__name__)
            logger.warning("telegram_poller_start_failed type=%s", type(exc).__name__)

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=6)
        if self.client is not None and self._owns_client:
            try:
                self.client.close()
            except Exception as exc:
                logger.warning("telegram_client_close_failed type=%s",type(exc).__name__)
        _set_runtime(False); logger.info("telegram_command_poller_stopped")
