from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from pydantic import SecretStr

from app.config.settings import AppSettings


GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
ALLOWED_VERDICTS = {"CONFIRM", "WATCH", "AVOID"}


def _secret_value(value: SecretStr | str | None) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value or ""


def ai_health(config: AppSettings) -> dict[str, Any]:
    return {
        "enabled": config.ai_enabled,
        "configured": bool(_secret_value(config.groq_api_key)) if config.ai_provider.lower() == "groq" else False,
        "provider": config.ai_provider.lower(),
        "model": config.groq_model,
        "execution_authority": False,
    }


def build_snapshot(symbol: str, price: Any, score: int, decision: str, details: dict[str, Any]) -> dict[str, Any]:
    timeframes = details.get("timeframes", {})
    setup = details.get("setup", {})
    momentum = details.get("momentum", {})
    volume = details.get("volume", {})
    volatility = details.get("volatility", {})
    levels = details.get("levels", {})
    context = details.get("analysis_context", {})
    structure = details.get("structure", {})
    return {
        "symbol": symbol,
        "price": float(price),
        "strategy_score": score,
        "strategy_decision": decision,
        "trend_1d": timeframes.get("1d", {}).get("label"),
        "structure_1h": structure.get("label") or timeframes.get("1h", {}).get("label"),
        "trigger_15m": setup.get("setup_type"),
        "setup": setup.get("setup_type"),
        "setup_quality": setup.get("score"),
        "rsi": momentum.get("rsi"),
        "momentum": momentum.get("label"),
        "rvol": volume.get("rvol"),
        "volume_quality": volume.get("quality"),
        "atr": volatility.get("atr"),
        "volatility": volatility.get("atr_pct"),
        "support": levels.get("support"),
        "resistance": levels.get("resistance"),
        "entry": setup.get("entry_area"),
        "stop": setup.get("invalidation_level"),
        "target": setup.get("target"),
        "rr": details.get("risk_reward"),
        "score_breakdown": details.get("score_breakdown", {}),
        "closed_candle_timestamps": {
            "1d": context.get("daily_candle_time"),
            "1h": context.get("hourly_candle_time"),
            "15m": context.get("entry_candle_time"),
        },
    }


def attach_opinion(analysis: Any, opinion: dict[str, Any]) -> None:
    """Attach advisory-only fields; deterministic trade fields are intentionally unreachable."""
    analysis.ai_status = opinion["status"]
    analysis.ai_provider = opinion.get("provider")
    analysis.ai_model = opinion.get("model")
    analysis.ai_result = opinion


class GroqAdvisor:
    def __init__(self, config: AppSettings, client: httpx.Client | None = None):
        self.config = config
        self.client = client or httpx.Client(timeout=config.ai_timeout_seconds)
        self._rate_limited_until = 0.0
        self._unavailable_until = 0.0

    def _base(self, status: str, reason: str) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "provider": self.config.ai_provider.lower(),
            "model": self.config.groq_model,
            "execution_authority": False,
        }

    def evaluate(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self.config.ai_enabled:
            return self._base("DISABLED", "AI kapalı")
        if int(snapshot.get("strategy_score") or 0) < self.config.ai_min_score:
            return self._base("SKIPPED_LOW_SCORE", "Score AI eşiğinin altında")
        if self.config.ai_provider.lower() != "groq":
            return self._base("AI_UNAVAILABLE", "AI provider desteklenmiyor")
        api_key = _secret_value(self.config.groq_api_key)
        if not api_key:
            return self._base("API_KEY_MISSING", "API key eksik")
        now = time.monotonic()
        if now < self._rate_limited_until:
            return self._base("RATE_LIMITED", "Groq rate limit")
        if now < self._unavailable_until:
            return self._base("AI_UNAVAILABLE", "Groq geçici olarak kullanılamıyor")

        payload = {
            "model": self.config.groq_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a second-opinion reviewer for deterministic BIST paper trading. "
                        "Do not calculate indicators. Never change or recommend changing the strategy decision, "
                        "score, entry, stop, target, position size, or any execution action. Return only a JSON object "
                        "with verdict (CONFIRM, WATCH, or AVOID), confidence (0-100), summary, strengths (array), "
                        "risks (array), and invalidation_note. Respond in Turkish."
                    ),
                },
                {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))},
            ],
            "temperature": 0.2,
            "max_completion_tokens": 500,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self.client.post(
                GROQ_CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.config.ai_timeout_seconds,
            )
        except httpx.TimeoutException:
            self._unavailable_until = time.monotonic() + 60
            return self._base("AI_UNAVAILABLE", "Groq timeout")
        except httpx.HTTPError:
            self._unavailable_until = time.monotonic() + 60
            return self._base("AI_UNAVAILABLE", "Groq bağlantı hatası")

        if response.status_code == 429:
            try:
                retry_after = min(300.0, max(1.0, float(response.headers.get("retry-after", "60"))))
            except ValueError:
                retry_after = 60.0
            self._rate_limited_until = time.monotonic() + retry_after
            return self._base("RATE_LIMITED", "Groq rate limit")
        if response.status_code == 401:
            return self._base("AUTH_ERROR", "Groq kimlik doğrulama hatası")
        if response.status_code >= 400:
            return self._base("AI_UNAVAILABLE", f"Groq provider HTTP {response.status_code}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
            cleaned = re.sub(r"^\s*```(?:json)?\s*", "", content, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```\s*$", "", cleaned)
            parsed = json.loads(cleaned)
            verdict = str(parsed["verdict"]).upper()
            confidence = parsed["confidence"]
            if verdict not in ALLOWED_VERDICTS or isinstance(confidence, bool):
                raise ValueError("invalid verdict or confidence")
            confidence = int(confidence)
            if not 0 <= confidence <= 100:
                raise ValueError("confidence out of range")
            if not isinstance(parsed.get("strengths", []), list) or not isinstance(parsed.get("risks", []), list):
                raise ValueError("strengths and risks must be arrays")
            strengths = [str(item)[:300] for item in parsed.get("strengths", []) if str(item).strip()][:5]
            risks = [str(item)[:300] for item in parsed.get("risks", []) if str(item).strip()][:5]
            return {
                **self._base("OK", "Groq analizi tamamlandı"),
                "verdict": verdict,
                "confidence": confidence,
                "summary": str(parsed.get("summary", ""))[:1000],
                "strengths": strengths,
                "risks": risks,
                "invalidation_note": str(parsed.get("invalidation_note", ""))[:500],
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._base("MALFORMED_RESPONSE", "Groq yanıtı geçersiz")
