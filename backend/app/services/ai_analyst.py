from __future__ import annotations

import json
from typing import Any

import httpx

from app.config.settings import AppSettings


class AIAnalyst:
    """Advisory AI layer. It never places orders or bypasses hard gates."""

    gemini_base = "https://generativelanguage.googleapis.com/v1beta/models"
    openai_endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, config: AppSettings):
        self.config = config

    @property
    def provider(self) -> str:
        return (self.config.ai_provider or "gemini").lower()

    @property
    def configured(self) -> bool:
        if not self.config.ai_enabled:
            return False
        if self.provider == "gemini":
            return bool(self.config.gemini_api_key)
        if self.provider == "openai":
            return bool(self.config.openai_api_key)
        return False

    @property
    def model(self) -> str:
        return self.config.gemini_model if self.provider == "gemini" else self.config.openai_model

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.ai_enabled,
            "configured": self.configured,
            "provider": self.provider,
            "model": self.model,
            "min_score": self.config.ai_min_score,
            "execution_authority": False,
        }

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        if not text:
            raise ValueError("AI response text is empty")
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI response has no JSON object")
        value = json.loads(text[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("AI response must be a JSON object")
        return value

    @staticmethod
    def _normalize_result(parsed: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
        verdict = str(parsed.get("verdict", "WATCH")).upper()
        if verdict not in {"CONFIRM", "WATCH", "AVOID"}:
            verdict = "WATCH"
        try:
            confidence = max(0, min(100, int(parsed.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0
        return {
            "status": "OK",
            "provider": provider,
            "model": model,
            "verdict": verdict,
            "confidence": confidence,
            "summary": str(parsed.get("summary", ""))[:500],
            "strengths": [str(x)[:180] for x in (parsed.get("strengths") or [])[:3]],
            "risks": [str(x)[:180] for x in (parsed.get("risks") or [])[:3]],
            "invalidation_note": str(parsed.get("invalidation_note", ""))[:300],
            "execution_authority": False,
        }

    @staticmethod
    def _prompt(symbol: str, score: int, decision: str, details: dict[str, Any]) -> str:
        technical_payload = {
            "symbol": symbol,
            "strategy_score": score,
            "strategy_decision": decision,
            "timeframes": details.get("timeframes"),
            "market_structure": details.get("structure"),
            "momentum": details.get("momentum"),
            "volume": details.get("volume"),
            "volatility": details.get("volatility"),
            "levels": details.get("levels"),
            "setup": details.get("setup"),
            "risk_reward": details.get("risk_reward"),
            "score_breakdown": details.get("score_breakdown"),
            "analysis_context": details.get("analysis_context"),
        }
        return (
            "Sen BIST paper-trading sistemi içindeki ikinci görüş AI analistisin. "
            "Emir veremezsin ve deterministik risk/veri kurallarını aşamazsın. "
            "Sadece aşağıdaki teknik snapshot'ı değerlendir. Haber, fiyat, indikatör, temel veri veya "
            "görmediğin grafik formasyonu uydurma. Yalnızca geçerli JSON döndür. Anahtarlar: "
            "verdict (CONFIRM|WATCH|AVOID), confidence (0-100 integer), summary (kısa Türkçe), "
            "strengths (en fazla 3 kısa Türkçe madde), risks (en fazla 3 kısa Türkçe madde), "
            "invalidation_note (kısa Türkçe).\n\nTEKNIK_SNAPSHOT:\n"
            + json.dumps(technical_payload, ensure_ascii=False, separators=(",", ":"))
        )

    def _analyze_gemini(self, prompt: str) -> dict[str, Any]:
        if not self.config.gemini_api_key:
            return {"status": "MISSING_API_KEY", "provider": "gemini", "execution_authority": False}

        url = f"{self.gemini_base}/{self.config.gemini_model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 500,
                "responseMimeType": "application/json",
            },
        }
        try:
            with httpx.Client(timeout=self.config.ai_timeout_seconds) as client:
                response = client.post(
                    url,
                    headers={
                        "x-goog-api-key": self.config.gemini_api_key,
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
            text = (
                payload.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            return self._normalize_result(
                self._parse_json(text), "gemini", self.config.gemini_model
            )
        except httpx.HTTPStatusError as exc:
            return {
                "status": "API_ERROR",
                "provider": "gemini",
                "http_status": exc.response.status_code,
                "execution_authority": False,
            }
        except Exception as exc:
            return {
                "status": "ERROR",
                "provider": "gemini",
                "error": type(exc).__name__,
                "execution_authority": False,
            }

    @staticmethod
    def _extract_openai_text(payload: dict[str, Any]) -> str:
        chunks: list[str] = []
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") == "output_text" and part.get("text"):
                    chunks.append(part["text"])
        return "\n".join(chunks).strip()

    def _analyze_openai(self, prompt: str) -> dict[str, Any]:
        if not self.config.openai_api_key:
            return {"status": "MISSING_API_KEY", "provider": "openai", "execution_authority": False}
        request_payload = {
            "model": self.config.openai_model,
            "reasoning": {"effort": "low"},
            "max_output_tokens": 500,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        }
        try:
            with httpx.Client(timeout=self.config.ai_timeout_seconds) as client:
                response = client.post(
                    self.openai_endpoint,
                    headers={
                        "Authorization": f"Bearer {self.config.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_payload,
                )
                response.raise_for_status()
                payload = response.json()
            return self._normalize_result(
                self._parse_json(self._extract_openai_text(payload)),
                "openai",
                self.config.openai_model,
            )
        except httpx.HTTPStatusError as exc:
            return {
                "status": "API_ERROR",
                "provider": "openai",
                "http_status": exc.response.status_code,
                "execution_authority": False,
            }
        except Exception as exc:
            return {
                "status": "ERROR",
                "provider": "openai",
                "error": type(exc).__name__,
                "execution_authority": False,
            }

    def analyze(self, symbol: str, score: int, decision: str, details: dict[str, Any]) -> dict[str, Any]:
        if not self.config.ai_enabled:
            return {"status": "DISABLED", "execution_authority": False}
        if score < self.config.ai_min_score:
            return {
                "status": "SKIPPED_LOW_SCORE",
                "provider": self.provider,
                "min_score": self.config.ai_min_score,
                "execution_authority": False,
            }

        prompt = self._prompt(symbol, score, decision, details)
        if self.provider == "gemini":
            return self._analyze_gemini(prompt)
        if self.provider == "openai":
            return self._analyze_openai(prompt)
        return {
            "status": "UNSUPPORTED_PROVIDER",
            "provider": self.provider,
            "execution_authority": False,
        }
