from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import threading
import uuid
from zoneinfo import ZoneInfo

logger = logging.getLogger("SIMPLE_STATE")
MODE = "SIMPLE_PAPER_V1"


def _default(initial_cash: Decimal) -> dict:
    cash = float(initial_cash)
    return {"mode": MODE, "initial_cash": cash, "cash": cash, "realized_pnl": 0.0,
        "open_position": None, "trades_today": 0, "daily_realized_pnl": 0.0,
        "daily_date": None, "daily_trades": [], "last_exit_by_symbol": {}, "last_scan": None,
        "last_processed_candle": None, "last_best_alert": None, "last_data_state": None,
        "daily_summary_sent_date": None, "loss_brake_alert_date": None,
        "database_status": "UNAVAILABLE", "database_checked": False, "state_backend": "MEMORY", "paused": False,
        "daily_start_equity": cash, "restart_alert_sent": False}


class SimpleStateStore:
    """Small crash-safe runtime store. PostgreSQL is deliberately not a dependency."""

    def __init__(self, path: str, initial_cash: Decimal, timezone_name: str):
        self.path = Path(path)
        self.initial_cash = Decimal(initial_cash)
        self.timezone = ZoneInfo(timezone_name)
        self.lock = threading.RLock()
        self.cold_started = False
        self.corrupt_recovered = False
        self.local_available = False
        self.state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("mode") != MODE:
                    raise ValueError("invalid simple state")
                merged = _default(self.initial_cash); merged.update(data)
                self.local_available = True
                merged["state_backend"] = "LOCAL_JSON"
                return merged
            except Exception as exc:
                self.corrupt_recovered = True
                logger.warning("simple_state_corrupt_recovered type=%s", type(exc).__name__)
        self.cold_started = True
        return _default(self.initial_cash)

    def _date(self, now: datetime) -> str:
        return now.astimezone(self.timezone).date().isoformat()

    def _roll_day(self, now: datetime) -> None:
        day = self._date(now)
        if self.state.get("daily_date") == day:
            return
        self.state["daily_date"] = day
        self.state["trades_today"] = 0
        self.state["daily_realized_pnl"] = 0.0
        self.state["daily_trades"] = []
        self.state["loss_brake_alert_date"] = None
        position = self.state.get("open_position")
        invested = float(position["current_price"])*int(position["quantity"]) if position else 0.0
        self.state["daily_start_equity"] = float(self.state.get("cash", 0))+invested

    def _persist_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(self.state, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self.local_available = True
            if self.state.get("database_status") != "OK":
                self.state["state_backend"] = "LOCAL_JSON"
        except Exception as exc:
            self.local_available = False
            self.state["state_backend"] = "MEMORY"
            logger.warning("simple_state_write_failed type=%s", type(exc).__name__)

    def read(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self.lock:
            self._roll_day(now)
            return deepcopy(self.state)

    def mutate(self, callback, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        with self.lock:
            self._roll_day(now)
            result = callback(self.state)
            self._persist_locked()
            return result

    def database(self, status: str) -> None:
        status = status if status in {"OK", "DEGRADED", "UNAVAILABLE"} else "DEGRADED"
        def update(state):
            state["database_status"] = status; state["database_checked"] = True
            state["state_backend"] = "DATABASE" if status == "OK" else "LOCAL_JSON" if self.local_available else "MEMORY"
        self.mutate(update)

    def replace_from_database(self, values: dict) -> None:
        def update(state):
            state.update(values); state["database_status"] = "OK"; state["state_backend"] = "DATABASE"
        self.mutate(update)


@lru_cache(maxsize=16)
def _cached_store(path: str, initial_cash: str, timezone_name: str) -> SimpleStateStore:
    return SimpleStateStore(path, Decimal(initial_cash), timezone_name)


def get_simple_state(config) -> SimpleStateStore:
    return _cached_store(config.simple_state_path, str(config.initial_balance), config.bist_timezone)


def clear_simple_state_cache() -> None:
    _cached_store.cache_clear()
