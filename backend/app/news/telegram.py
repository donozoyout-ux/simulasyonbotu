from __future__ import annotations

import httpx

from app.ai.groq_advisor import _secret_value


class TelegramNewsNotifier:
    def __init__(self, config, client: httpx.Client | None = None):
        self.config = config
        self.client = client or httpx.Client(timeout=10)

    @property
    def configured(self) -> bool:
        return bool(self.config.telegram_enabled and _secret_value(self.config.telegram_bot_token) and _secret_value(self.config.telegram_chat_id))

    def send(self, item) -> bool:
        if (not self.configured or not getattr(item, "telegram_eligible", True) or item.telegram_sent
                or (item.ai_importance or 0) < self.config.news_telegram_min_importance):
            return False
        token, chat_id = _secret_value(self.config.telegram_bot_token), _secret_value(self.config.telegram_chat_id)
        text = (f"📢 KAP HABERİ\n\n{item.symbol or 'BIST'}\n\n{item.title}\n\nEtki: {item.ai_sentiment or 'NEUTRAL'}\n"
                f"Önem: {item.ai_importance}/100\n\nAI: {item.ai_summary or 'Özet yok'}\n\nKaynak: {item.source}")
        try:
            response = self.client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})
            return response.is_success
        except httpx.HTTPError:
            return False
