from __future__ import annotations

import json
from typing import Any

import httpx

from app.config.settings import AppSettings


class AIAnalyst:
    """Advisory AI layer. It never places orders or bypasses hard gates."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, config: AppSettings):
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(self.config.ai_enabled and self.config.openai_api_key)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.ai_enabled,
            "configured": self.configured,
            "model": self.config.openai_model,
            "min_score": self.config.ai_min_score,
            "execution_authority": False,
        }

    @staticmethod
    def _extract_output_text(payload: dict[str, Any]) -> str:
        chunks: list[str] = []
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") == "output_text" and part.get("text"):
                    chunks.append(part["text"])
        return "\n".join(chunks).strip()

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

    def analyze(self, symbol: str, score: int, decision: str, details: dict[str, Any]) -> dict[str, Any]:
        if not self.config.ai_enabled:
            return {"status": "DISABLED", "execution_authority": False}
        if not self.config.openai_api_key:
            return {"status": "MISSING_API_KEY", "execution_authority": False}
        if score < self.config.ai_min_score:
            return {
                "status": "SKIPPED_LOW_SCORE",
                "min_score": self.config.ai_min_score,
                "execution_authority": False,
            }

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

        system_prompt = (
            "You are the second-opinion AI analyst inside a BIST paper-trading simulator. "
            "You do not place orders and cannot override hard risk/data gates. "
            "Evaluate only the supplied technical snapshot. Do not invent news, prices, indicators, "
            "fundamentals, or unseen chart patterns. Return only valid JSON with keys: "
            "verdict (CONFIRM|WATCH|AVOID), confidence (integer 0-100), summary (short Turkish text), "
            "strengths (array max 3 Turkish strings), risks (array max 3 Turkish strings), "
            "invalidation_note (short Turkish string)."
        )

        request_payload = {
            "model": self.config.openai_model,
            "reasoning": {"effort": "low"},
            "max_output_tokens": 500,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(technical_payload, ensure_ascii=False, separators=(",", ":")),
                        }
                    ],
                },
            ],
        }

        try:
            with httpx.Client(timeout=self.config.ai_timeout_seconds) as client:
                response = client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.config.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_payload,
                )
                response.raise_for_status()
                payload = response.json()
            parsed = self._parse_json(self._extract_output_text(payload))
            verdict = str(parsed.get("verdict", "WATCH")).upper()
            if verdict not in {"CONFIRM", "WATCH", "AVOID"}:
                verdict = "WATCH"
            try:
                confidence = max(0, min(100, int(parsed.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0
            return {
                "status": "OK",
                "model": self.config.openai_model,
                "verdict": verdict,
                "confidence": confidence,
                "summary": str(parsed.get("summary", ""))[:500],
                "strengths": [str(x)[:180] for x in (parsed.get("strengths") or [])[:3]],
                "risks": [str(x)[:180] for x in (parsed.get("risks") or [])[:3]],
                "invalidation_note": str(parsed.get("invalidation_note", ""))[:300],
                "execution_authority": False,
            }
        except httpx.HTTPStatusError as exc:
            return {
                "status": "API_ERROR",
                "http_status": exc.response.status_code,
                "execution_authority": False,
            }
        except Exception as exc:
            return {
                "status": "ERROR",
                "error": type(exc).__name__,
                "execution_authority": False,
            }
