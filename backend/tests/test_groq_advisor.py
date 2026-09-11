import json
from types import SimpleNamespace

import httpx
import pytest

from app.ai.groq_advisor import GROQ_CHAT_COMPLETIONS_URL, GroqAdvisor, ai_health, attach_opinion
from app.config.settings import AppSettings


def config(**changes):
    values={"ai_enabled":True,"ai_provider":"groq","groq_model":"openai/gpt-oss-20b","ai_min_score":70}
    values.update(changes)
    return AppSettings(**values)


def snapshot(score=82):
    return {"symbol": "TEST", "price": 100, "strategy_score": score, "strategy_decision": "POSSIBLE_ENTRY"}


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def valid_response(extra=None, fenced=False):
    body={"verdict":"CONFIRM","confidence":82,"summary":"Yapı uyumlu","strengths":["Trend"],
          "risks":["Hacim"],"invalidation_note":"Stop altında geçersiz",**(extra or {})}
    content=json.dumps(body)
    if fenced:content=f"```json\n{content}\n```"
    return {"choices":[{"message":{"content":content}}]}


def test_groq_api_key_missing():
    result=GroqAdvisor(config(groq_api_key=None)).evaluate(snapshot())
    assert result["status"]=="API_KEY_MISSING" and result["execution_authority"] is False


def test_groq_configured_health_never_exposes_key():
    health=ai_health(config(groq_api_key="super-secret"))
    assert health=={"enabled":True,"configured":True,"provider":"groq","model":"openai/gpt-oss-20b","execution_authority":False}
    assert "secret" not in json.dumps(health)


def test_score_below_ai_min_score_is_skipped_without_http():
    called=False
    def handler(request):
        nonlocal called;called=True;return httpx.Response(500,request=request)
    result=GroqAdvisor(config(groq_api_key="key"),client(handler)).evaluate(snapshot(69))
    assert result["status"]=="SKIPPED_LOW_SCORE" and called is False


def test_score_at_ai_min_score_calls_groq_with_expected_contract():
    def handler(request):
        assert str(request.url)==GROQ_CHAT_COMPLETIONS_URL
        payload=json.loads(request.content)
        assert payload["model"]=="openai/gpt-oss-20b"
        assert payload["temperature"]==0.2 and payload["max_completion_tokens"]==500
        return httpx.Response(200,json=valid_response(),request=request)
    assert GroqAdvisor(config(groq_api_key="key"),client(handler)).evaluate(snapshot(70))["status"]=="OK"


def test_valid_groq_json_and_markdown_fence_are_parsed():
    advisor=GroqAdvisor(config(groq_api_key="key"),client(lambda request:httpx.Response(200,json=valid_response(fenced=True),request=request)))
    result=advisor.evaluate(snapshot())
    assert result["verdict"]=="CONFIRM" and result["confidence"]==82
    assert result["execution_authority"] is False


def test_malformed_groq_json_is_safe():
    payload={"choices":[{"message":{"content":"```json\nnot-json\n```"}}]}
    advisor=GroqAdvisor(config(groq_api_key="key"),client(lambda request:httpx.Response(200,json=payload,request=request)))
    assert advisor.evaluate(snapshot())["status"]=="MALFORMED_RESPONSE"


def test_groq_timeout_is_advisory_only():
    def handler(request):raise httpx.ReadTimeout("timeout",request=request)
    assert GroqAdvisor(config(groq_api_key="key"),client(handler)).evaluate(snapshot())["status"]=="AI_UNAVAILABLE"


def test_http_429_sets_rate_limited_status_and_circuit():
    calls=0
    def handler(request):
        nonlocal calls;calls+=1;return httpx.Response(429,headers={"retry-after":"30"},request=request)
    advisor=GroqAdvisor(config(groq_api_key="key"),client(handler))
    assert advisor.evaluate(snapshot())["status"]=="RATE_LIMITED"
    assert advisor.evaluate(snapshot())["status"]=="RATE_LIMITED" and calls==1


def test_http_401_is_auth_error_without_secret_logging(caplog):
    advisor=GroqAdvisor(config(groq_api_key="never-log-this"),client(lambda request:httpx.Response(401,request=request)))
    assert advisor.evaluate(snapshot())["status"]=="AUTH_ERROR"
    assert "never-log-this" not in caplog.text


def test_groq_down_does_not_raise():
    def handler(request):raise httpx.ConnectError("down",request=request)
    assert GroqAdvisor(config(groq_api_key="key"),client(handler)).evaluate(snapshot())["status"]=="AI_UNAVAILABLE"


def test_ai_disabled_never_calls_http():
    result=GroqAdvisor(config(ai_enabled=False,groq_api_key="key")).evaluate(snapshot())
    assert result["status"]=="DISABLED"


@pytest.mark.parametrize("protected_field",["decision","stop_price","target_price","quantity"])
def test_ai_cannot_alter_deterministic_trade_fields(protected_field):
    analysis=SimpleNamespace(decision="POSSIBLE_ENTRY",stop_price=95,target_price=110,quantity=5,
        ai_status=None,ai_provider=None,ai_model=None,ai_result=None)
    before=getattr(analysis,protected_field)
    malicious={**GroqAdvisor(config())._base("OK","test"),"verdict":"AVOID","confidence":99,
        "decision":"NO_TRADE","stop_price":99,"target_price":101,"quantity":0}
    attach_opinion(analysis,malicious)
    assert getattr(analysis,protected_field)==before
    assert analysis.ai_result["execution_authority"] is False
