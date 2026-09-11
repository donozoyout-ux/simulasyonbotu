from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.ai.groq_advisor import GROQ_CHAT_COMPLETIONS_URL, _secret_value


SENTIMENTS = {"POSITIVE", "NEUTRAL", "NEGATIVE"}
HORIZONS = {"INTRADAY", "SHORT_TERM", "MEDIUM_TERM"}


class GroqNewsAnalyzer:
    def __init__(self, config, client: httpx.Client | None = None):
        self.config = config
        self.client = client or httpx.Client(timeout=config.ai_timeout_seconds)
        self.rate_limited_until = 0.0

    def _base(self, status: str) -> dict[str, Any]:
        return {"status": status, "provider": "groq", "model": self.config.groq_model, "execution_authority": False}

    def evaluate(self, article: dict[str, Any]) -> dict[str, Any]:
        if not self.config.news_enabled or not self.config.ai_enabled:
            return self._base("DISABLED")
        key = _secret_value(self.config.groq_api_key)
        if not key:
            return self._base("API_KEY_MISSING")
        if time.monotonic() < self.rate_limited_until:
            return self._base("RATE_LIMITED")
        safe_article = {key: article.get(key) for key in ("symbol", "source", "title", "content", "category", "published_at")}
        payload = {
            "model": self.config.groq_model,
            "messages": [
                {"role": "system", "content": "Only evaluate supplied article/disclosure. Do not invent facts. Return Turkish JSON with sentiment (POSITIVE|NEUTRAL|NEGATIVE), importance (0-100), summary, horizon (INTRADAY|SHORT_TERM|MEDIUM_TERM), risks array, tags array. You have no trading or execution authority."},
                {"role": "user", "content": json.dumps(safe_article, ensure_ascii=False, default=str)},
            ],
            "temperature": 0.1,
            "max_completion_tokens": 450,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self.client.post(GROQ_CHAT_COMPLETIONS_URL, headers={"Authorization": f"Bearer {key}"}, json=payload)
        except httpx.TimeoutException:
            return self._base("AI_UNAVAILABLE")
        except httpx.HTTPError:
            return self._base("AI_UNAVAILABLE")
        if response.status_code == 429:
            self.rate_limited_until = time.monotonic() + 60
            return self._base("RATE_LIMITED")
        if response.status_code == 401:
            return self._base("AUTH_ERROR")
        if response.status_code >= 400:
            return self._base("AI_UNAVAILABLE")
        try:
            raw = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(re.sub(r"(^\s*```(?:json)?\s*|\s*```\s*$)", "", raw, flags=re.I))
            sentiment = str(parsed["sentiment"]).upper()
            horizon = str(parsed["horizon"]).upper()
            importance = int(parsed["importance"])
            if sentiment not in SENTIMENTS or horizon not in HORIZONS or not 0 <= importance <= 100:
                raise ValueError
            if not isinstance(parsed.get("risks", []), list) or not isinstance(parsed.get("tags", []), list):
                raise ValueError
            return {**self._base("OK"), "sentiment": sentiment, "importance": importance,
                "summary": str(parsed.get("summary", ""))[:1000], "horizon": horizon,
                "risks": [str(x)[:250] for x in parsed.get("risks", [])[:5]],
                "tags": [str(x)[:80] for x in parsed.get("tags", [])[:8]]}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._base("MALFORMED_RESPONSE")

    def combined(self, technical: dict, news: list[dict]) -> dict[str, Any]:
        """Conservative local synthesis; it cannot mutate or gate deterministic strategy state."""
        technical_verdict = technical.get("verdict") if technical.get("status") == "OK" else "WATCH"
        sentiments = [item.get("ai_sentiment") for item in news if item.get("ai_sentiment")]
        news_sentiment = "NO_NEWS" if not sentiments else ("NEGATIVE" if "NEGATIVE" in sentiments else "POSITIVE" if "POSITIVE" in sentiments else "NEUTRAL")
        combined_view = "AVOID" if technical_verdict == "AVOID" else "WATCH" if news_sentiment in {"NEGATIVE", "NO_NEWS"} else technical_verdict
        return {"technical_verdict": technical_verdict, "news_sentiment": news_sentiment,
            "combined_view": combined_view, "confidence": int(technical.get("confidence") or 0),
            "summary": "Teknik ikinci görüş ve mevcut haber özeti birlikte gösterilir; strateji kararını değiştirmez.",
            "main_risks": [risk for item in news for risk in (item.get("ai_risks") or [])][:5],
            "execution_authority": False}
