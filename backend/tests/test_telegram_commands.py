from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import AppSettings
from app.api.routes import data_health, telegram_status
from app.db.session import Base
from app.models import DecisionLog, ForwardRun, NewsItem, Portfolio, ScanRun, WatchlistItem
from app.services.embedded_worker import EmbeddedWorker
from app.services.forward_test import ensure_forward_run
from app.services.system_health import classify_data_health
from app.services.telegram_alerts import TelegramDataHealthAlerter
from app.services.telegram_commands import COMMANDS, TelegramCommandPoller, TelegramCommandService


def config(**changes):
    values = {"data_mode":"live", "market_data_provider":"hybrid", "operation_mode":"LIVE_PAPER",
        "ai_enabled":False, "telegram_enabled":True, "telegram_bot_token":"fake-token-value",
        "telegram_chat_id":"123", "telegram_commands_enabled":True, "telegram_startup_alert":False,
        "embedded_worker_enabled":False, "news_enabled":False, "market_memory_enabled":False}
    values.update(changes)
    return AppSettings(_env_file=None, **values)


def database():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def update(update_id, text, chat="123"):
    return {"update_id":update_id,"message":{"chat":{"id":chat},"text":text}}


def scan(run_id, valid=22, failed=8, mode="LIVE", errors=None):
    return ScanRun(started_at=datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc),duration_ms=12,
        provider="hybrid",data_mode="live",total_symbols=valid+failed,valid_symbols=valid,failed_symbols=failed,
        stale_symbols=0,funnel={"score_highest":76,"score_average":55,"score_above_watchlist":2,
            "score_above_entry":0,"quarantined_skipped":1},errors=errors or [],run_id=run_id,
        watchlist_count=2,signals=0,entries=0,analysis_mode=mode,market_open=mode=="LIVE")


def test_authorization_help_ping_unknown_and_persisted_dedupe():
    cfg=config(); engine=database()
    with Session(engine) as db:
        ensure_forward_run(db,cfg); service=TelegramCommandService(db,cfg)
        assert service.handle_update(update(1,"/ping")) and "BIST BOT ONLINE" in service.dispatch("ping")
        assert service.handle_update(update(2,"/help@mybot")) and "/portfolio" in service.dispatch("help")
        assert "Bilinmeyen" in service.handle_update(update(3,"/doesnotexist"))
        assert service.handle_update(update(4,"/status",chat="999")) is None
        assert service.handle_update(update(1,"/pause")) is None
        assert db.scalar(select(func.count()).select_from(DecisionLog).where(
            DecisionLog.category=="TELEGRAM_COMMAND",DecisionLog.reason=="UPDATE:1"))==1


def test_status_portfolio_positions_watchlist_scanner_health_news_and_alerts_are_bounded_local():
    cfg=config(); engine=database(); now=datetime.now(timezone.utc)
    with Session(engine) as db:
        run=ensure_forward_run(db,cfg)
        db.add_all([WatchlistItem(symbol=f"S{i:02d}",score=99-i,setup="NONE",status="WATCHING",
            reason="ok",run_id=run.run_id) for i in range(12)])
        db.add(scan(run.run_id,errors=[{"symbol":"UTPYA","type":"SYMBOL_NOT_FOUND"}]))
        db.add_all([NewsItem(symbol="ASELS",source="KAP",source_id=f"n{i}",title=f"News {i}",content="long",
            url="https://example.invalid",category="OTHER",status="OK",published_at=now,
            content_hash=f"hash{i}",ai_sentiment="POSITIVE",ai_importance=90) for i in range(6)])
        db.commit(); service=TelegramCommandService(db,cfg)
        assert "V3_FROZEN_1" in service.dispatch("status")
        assert "Initial:" in service.dispatch("portfolio")
        assert service.dispatch("positions")=="📭 Açık paper pozisyon yok."
        watch=service.dispatch("watchlist"); assert "S00" in watch and "S09" in watch and "S10" not in watch
        scanner=service.dispatch("scanner"); assert "UTPYA — SYMBOL_NOT_FOUND" in scanner
        with patch("app.market_data.yahoo_provider.YahooMarketDataProvider.get_candles",side_effect=AssertionError("provider called")):
            assert "Backend: OK" in service.dispatch("health")
        news=service.dispatch("news"); assert news.count("<b>ASELS • KAP</b>")==5
        alerts=service.dispatch("alerts"); assert "Commands: ON" in alerts and "Off-hours signal alerts: OFF" in alerts


def test_pause_resume_same_run_without_portfolio_reset_or_new_run():
    cfg=config(); engine=database()
    with Session(engine) as db:
        run=ensure_forward_run(db,cfg); portfolio=db.get(Portfolio,1)
        portfolio.cash_balance=Decimal("4321.00"); db.commit(); service=TelegramCommandService(db,cfg)
        assert "PAUSED" in service.handle_update(update(10,"/pause")); db.refresh(run); assert run.paused
        assert "RESUMED" in service.handle_update(update(11,"/resume")); db.refresh(run); assert not run.paused
        assert db.scalar(select(func.count()).select_from(ForwardRun))==1
        assert db.get(Portfolio,1).cash_balance==Decimal("4321.00")
    empty_engine=database()
    with Session(empty_engine) as db:
        assert "Yeni run oluşturulmadı" in TelegramCommandService(db,cfg).dispatch("resume")
        assert db.scalar(select(func.count()).select_from(ForwardRun))==0


class FailingClient:
    def get(self,*_args,**_kwargs): raise RuntimeError("network failed fake-token-value")


def test_telegram_api_error_isolated_and_token_not_logged(caplog):
    cfg=config(); maker=sessionmaker(database(),expire_on_commit=False)
    poller=TelegramCommandPoller(cfg,session_factory=maker,client=FailingClient())
    assert poller.poll_once()["status"]=="ERROR"
    assert "fake-token-value" not in caplog.text


class Response:
    def __init__(self,body): self.body=body
    def raise_for_status(self): return None
    def json(self): return self.body


class TelegramApiClient:
    def __init__(self): self.posts=[]; self.get_params=None
    def get(self,_url,params=None,timeout=None):
        self.get_params=(params,timeout)
        return Response({"ok":True,"result":[update(21,"/ping")]})
    def post(self,url,json=None):
        self.posts.append((url.rsplit("/",1)[-1],json))
        return Response({"ok":True})


def test_mock_telegram_api_poll_reply_commands_and_startup():
    cfg=config(telegram_startup_alert=True); engine=database(); maker=sessionmaker(engine,expire_on_commit=False)
    with maker() as db: ensure_forward_run(db,cfg)
    client=TelegramApiClient(); poller=TelegramCommandPoller(cfg,session_factory=maker,client=client)
    poller.register_commands(); assert len(client.posts[0][1]["commands"])==len(COMMANDS)
    assert poller.send_startup_alert()["status"]=="SENT"
    assert poller.poll_once()=={"status":"OK","updates":1}
    assert client.get_params[0]["allowed_updates"]=='["message"]' and client.get_params[1]==5
    assert any(method=="sendMessage" and "BIST BOT ONLINE" in payload["text"] for method,payload in client.posts)


class FakeNotifier:
    configured=True
    def __init__(self): self.messages=[]
    def send(self,text): self.messages.append(text); return {"status":"SENT"}


def test_data_error_alert_deduped_and_recovery_sent_once():
    cfg=config(); engine=database(); notifier=FakeNotifier()
    with Session(engine) as db:
        run=ensure_forward_run(db,cfg)
        bad=scan(run.run_id,valid=4,failed=26,errors=[{"symbol":"UTPYA","type":"SYMBOL_NOT_FOUND"}])
        db.add(bad); db.commit(); alerter=TelegramDataHealthAlerter(db,cfg,notifier)
        assert alerter.notify_scan(bad)["status"]=="SENT"
        assert alerter.notify_scan(bad)["status"]=="DEDUPED"
        good=scan(run.run_id,valid=28,failed=2); db.add(good); db.commit()
        assert alerter.notify_scan(good)["status"]=="SENT"
        assert alerter.notify_scan(good)["status"]=="DEDUPED"
        assert len(notifier.messages)==2 and "DATA ERROR" in notifier.messages[0] and "DATA RECOVERED" in notifier.messages[1]


def test_live_and_off_hours_health_severity_rules():
    critical=SimpleNamespace(valid_symbols=0,failed_symbols=30,total_symbols=30,stale_symbols=0,analysis_mode="LIVE")
    assert classify_data_health(critical,True)=={"status":"DATA_ERROR","severity":"CRITICAL","system_status":"DATA_ERROR"}
    critical.analysis_mode="ANALYSIS_ONLY"
    assert classify_data_health(critical,False)=={"status":"DATA_ERROR","severity":"WARNING","system_status":"DATA_DEGRADED"}


def test_data_health_contract_and_telegram_status_are_extended_without_secrets():
    cfg=config(); engine=database()
    with Session(engine) as db, patch("app.api.routes.config",cfg), \
            patch("app.api.routes.BistMarketSession.is_open",return_value=True):
        ensure_forward_run(db,cfg)
        health=data_health(db)
        assert health["status"]=="NO_SCAN" and health["severity"]=="OK" and health["system_status"]=="LIVE_PAPER"
        status=telegram_status()
        assert {"enabled","configured","signal_alerts","commands_enabled","command_poller_running","data_health_alerts"}<=status.keys()
        assert "token" not in status and "chat_id" not in status


def test_worker_error_alert_is_rate_limited_and_safe():
    worker=EmbeddedWorker(config()); sent=[]
    with patch("app.services.embedded_worker.TelegramNotifier") as notifier:
        notifier.return_value.send.side_effect=lambda text: sent.append(text) or {"status":"SENT"}
        assert worker._notify_worker_failure(RuntimeError("secret raw detail"),now=1000)
        assert not worker._notify_worker_failure(RuntimeError("again"),now=2000)
        assert worker._notify_worker_failure(ValueError("later"),now=2801)
    assert len(sent)==2 and "secret raw detail" not in "".join(sent)
