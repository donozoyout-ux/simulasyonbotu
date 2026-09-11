from __future__ import annotations

from html import escape
from typing import Any

import httpx

from app.config.settings import AppSettings


class TelegramNotifier:
    def __init__(self, config: AppSettings):
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(
            self.config.telegram_enabled
            and self.config.telegram_bot_token
            and self.config.telegram_chat_id
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.telegram_enabled,
            "configured": self.configured,
            "signal_alerts": self.config.telegram_signal_alerts,
        }

    def send(self, text: str) -> dict[str, Any]:
        if not self.config.telegram_enabled:
            return {"status": "DISABLED"}
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            return {"status": "MISSING_CONFIG"}

        token = self.config.telegram_bot_token.get_secret_value()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            with httpx.Client(timeout=self.config.telegram_timeout_seconds) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                body = response.json()
            if not body.get("ok"):
                return {"status": "API_ERROR"}
            return {"status": "SENT"}
        except httpx.HTTPStatusError as exc:
            return {"status": "HTTP_ERROR", "http_status": exc.response.status_code}
        except Exception as exc:
            return {"status": "ERROR", "error": type(exc).__name__}

    @staticmethod
    def signal_message(symbol: str, score: int, setup: str, price: Any, rr: Any, ai: dict[str, Any] | None = None) -> str:
        ai = ai or {}
        verdict = ai.get("verdict")
        confidence = ai.get("confidence")
        ai_line = ""
        if verdict:
            ai_line = f"\n🤖 AI: <b>{escape(str(verdict))}</b>"
            if confidence is not None:
                ai_line += f" %{int(confidence)}"
        rr_text = "—" if rr is None else str(rr)
        return (
            f"🔎 <b>GÜÇLÜ SİNYAL</b>\n"
            f"<b>{escape(symbol)}</b>\n"
            f"Skor: <b>{score}</b>\n"
            f"Setup: {escape(setup.replace('_', ' '))}\n"
            f"Fiyat: {escape(str(price))}\n"
            f"RR: {escape(rr_text)}"
            f"{ai_line}"
        )

    @staticmethod
    def buy_message(symbol: str, quantity: int, price: Any, stop: Any, target: Any, score: int, setup: str) -> str:
        return (
            f"🟢 <b>PAPER BUY</b>\n"
            f"<b>{escape(symbol)}</b>\n"
            f"Lot: <b>{quantity}</b>\n"
            f"Giriş: {escape(str(price))}\n"
            f"Stop: {escape(str(stop))}\n"
            f"Hedef: {escape(str(target))}\n"
            f"Skor: {score}\n"
            f"Setup: {escape(setup.replace('_', ' '))}"
        )

    @staticmethod
    def sell_message(symbol: str, quantity: int, exit_price: Any, pnl: Any, reason: str) -> str:
        icon = "✅" if str(pnl).startswith("-") is False else "🔴"
        return (
            f"{icon} <b>PAPER SELL</b>\n"
            f"<b>{escape(symbol)}</b>\n"
            f"Lot: <b>{quantity}</b>\n"
            f"Çıkış: {escape(str(exit_price))}\n"
            f"K/Z: <b>{escape(str(pnl))}</b> TL\n"
            f"Neden: {escape(reason)}"
        )
