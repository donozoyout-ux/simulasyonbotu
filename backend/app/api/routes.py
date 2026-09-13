from decimal import Decimal
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.ai.groq_advisor import ai_health
from app.analysis.indicators import indicator_series
from app.db.session import get_db
from app.models import Analysis, Candle, DailySummary, DecisionLog, ForwardRun, Portfolio, PortfolioSnapshot, Position, ScanRun, Setting, SymbolHealth, Trade, WatchlistItem
from app.market_data.market_session import BistMarketSession
from app.portfolio.portfolio_manager import ensure_portfolio, portfolio_summary, take_snapshot
from app.schemas.common import PaperTradingControl, PortfolioReset, SettingsUpdate
from app.scanner.bist_scanner import BistScanner, effective_settings
from app.services.forward_test import active_forward_run,ensure_forward_run,forward_performance,reset_forward_run,set_paused
from app.services.forward_worker import expected_closed_candle
from app.services.telegram import TelegramNotifier
from app.news.service import NewsService
from app.news.reconciliation import NewsSymbolReconciliationService
from app.market_memory.backfill import BackfillService
from app.market_memory.service import MarketMemoryService
from app.services.collection_activity import recent_activity

router = APIRouter()
config = get_settings()
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def dump(value):
    return jsonable_encoder(value, custom_encoder={Decimal: lambda x: float(x)})


def current_run(db:Session)->ForwardRun:
    return ensure_forward_run(db,config)


def scan_data_health_status(run: ScanRun) -> str:
    if not run.failed_symbols and not run.stale_symbols:
        return "OK"
    if run.failed_symbols and (run.valid_symbols == 0 or run.failed_symbols > run.total_symbols / 2):
        return "DATA_ERROR"
    return "PARTIAL"


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1)); return {"status": "healthy", "mode": config.data_mode.upper(), "provider": config.market_data_provider,
        "real_orders": False,"ai":ai_health(config),"telegram":TelegramNotifier(config).status()}


@router.get("/ai/status")
def ai_status():
    return ai_health(config)


@router.get("/telegram/status")
def telegram_status():
    return TelegramNotifier(config).status()


@router.post("/telegram/test")
def telegram_test():
    notifier=TelegramNotifier(config)
    result=notifier.send(
        "✅ <b>BIST PILOT Telegram bağlantısı çalışıyor.</b>\n"
        f"Mode: {config.operation_mode}\n"
        f"Data: {config.market_data_provider}\n"
        "Gerçek emirler kapalı • PAPER TRADING"
    )
    if result.get("status")!="SENT":
        raise HTTPException(503,result)
    return result


@router.get("/data-health")
def data_health(db: Session = Depends(get_db)):
    forward=active_forward_run(db)
    run=db.scalar(select(ScanRun).where(ScanRun.run_id==forward.run_id).order_by(desc(ScanRun.started_at)).limit(1)) if forward else None
    market_open=BistMarketSession.from_config(config).is_open()
    if not run:return {"provider":config.market_data_provider,"mode":config.data_mode.upper(),"status":"NO_SCAN","market_open":market_open,"analysis_mode":"LIVE" if market_open else "ANALYSIS_ONLY","valid_symbols":0,"failed_symbols":0,"stale_symbols":0,"errors":[]}
    return dump({"provider":run.provider,"mode":run.data_mode.upper(),"status":scan_data_health_status(run),
        "market_open":market_open,"analysis_mode":run.analysis_mode,
        "last_successful_fetch":run.completed_at,"scanner_last_run":run.started_at,"scanner_duration_ms":run.duration_ms,
        "valid_symbols":run.valid_symbols,"failed_symbols":run.failed_symbols,"stale_symbols":run.stale_symbols,
        "errors":run.errors,"funnel":run.funnel})


@router.get("/strategy-health")
def strategy_health():
    paths={"before":DATA_DIR/"v3_baseline_report.json","after":DATA_DIR/"v3_after_report.json","portfolio":DATA_DIR/"v3_portfolio_report.json"}
    if not all(path.exists() for path in paths.values()):return {"status":"NO_RESEARCH_REPORT"}
    payload={key:json.loads(path.read_text(encoding="utf-8")) for key,path in paths.items()}
    before=payload["before"]["research"];after=payload["after"]["research"];portfolio=payload["portfolio"]["portfolio_replay"]
    response={"status":"READY","dataset":payload["after"]["dataset"],"score_before":before["score"],"score_after":after["score"],
        "setup_counts":{key:value["confirmed"] for key,value in after["setup_funnels"].items()},
        "signal_funnel":portfolio["funnel"],"top_rejections":sorted(portfolio["rejections"].items(),key=lambda item:item[1],reverse=True)[:5],
        "last_replay":{key:portfolio[key] for key in ("starting_equity","ending_equity","net_pnl","return_pct","trades","wins","losses","max_drawdown_pct","benchmark_return_pct")}}
    v4_path=DATA_DIR/"v4_oos_report.json"
    if v4_path.exists():
        v4=json.loads(v4_path.read_text(encoding="utf-8"));integrity=v4.get("data_integrity",{});trigger=integrity.get("timeframes",{}).get("15m",{})
        oos=v4.get("portfolio_oos",{});dataset=v4.get("dataset",{})
        response["v4"]={"status":v4.get("status"),"strategy_config_hash":v4.get("strategy_config",{}).get("sha256"),
            "research_period":{"start":payload["after"]["dataset"].get("start"),"end":payload["after"]["dataset"].get("end")},
            "oos_period":{"start":dataset.get("start"),"end":dataset.get("end")},"warmup_rows":trigger.get("warmup_rows",0),
            "evaluation_rows":trigger.get("evaluation_rows",0),"oos_trade_count":oos.get("trades",0),
            "sample_confidence":v4.get("sample_confidence"),"exit_diagnostics":oos.get("exit_diagnostics") or v4.get("v3_reference_exit_diagnostics"),
            "exit_diagnostics_scope":"OOS" if oos.get("exit_diagnostics") else "V3_REFERENCE"}
    v5_qualification=DATA_DIR/"v5_provider_qualification.json"
    if v5_qualification.exists():
        qualification=json.loads(v5_qualification.read_text(encoding="utf-8"))
        small_mid=qualification.get("twelvedata_small_mid",{})
        response["v5"]={"status":qualification.get("status"),"selected_primary":qualification.get("selected_primary"),
            "validation_provider":qualification.get("validation_provider"),"config_hash":qualification.get("config_hash"),
            "small_mid":{"status":small_mid.get("status"),"requested":small_mid.get("small_mid_symbols_requested",0),
                "valid":small_mid.get("small_mid_valid",0),"oos_capable":small_mid.get("15m_oos_capable",0)},
            "replay_gate":qualification.get("replay_gate")}
    return response


@router.get("/provider-qualification")
def provider_qualification():
    path=DATA_DIR/"v5_provider_qualification.json"
    if not path.exists():return {"status":"NO_PROVIDER_QUALIFICATION_REPORT"}
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/strategy-health/snapshots")
def strategy_snapshots(limit:int=Query(20,ge=1,le=50)):
    path=DATA_DIR/"v3_after_report.json"
    if not path.exists():return []
    return json.loads(path.read_text(encoding="utf-8"))["research"].get("snapshots",[])[:limit]


@router.get("/portfolio")
def portfolio(db: Session = Depends(get_db)): return dump(portfolio_summary(db, config.initial_balance))


@router.get("/forward/status")
def forward_status(db:Session=Depends(get_db)):
    run=current_run(db);now=datetime.now(timezone.utc);session=BistMarketSession.from_config(config)
    scans=list(db.scalars(select(ScanRun).where(ScanRun.run_id==run.run_id).order_by(desc(ScanRun.started_at))).all())
    last=scans[0] if scans else None;summary=portfolio_summary(db,config.initial_balance)
    today=now.astimezone(session.tz).date()
    trades_today=db.scalar(select(func.count()).select_from(Trade).where(Trade.run_id==run.run_id,
        Trade.exit_time>=datetime.combine(today,datetime.min.time(),session.tz).astimezone(timezone.utc))) or 0
    daily=db.scalar(select(DailySummary).where(DailySummary.run_id==run.run_id,DailySummary.date==today.isoformat()))
    watchlist_count=db.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.run_id==run.run_id)) or 0
    performance=forward_performance(db,config,run)
    last_closed=expected_closed_candle(config,now)
    return dump({"status":"PAUSED" if run.paused else "RUNNING","run_id":run.run_id,"forward_test_started_at":run.started_at,
        "running_days":max(0,(now-(run.started_at.replace(tzinfo=timezone.utc) if run.started_at.tzinfo is None else run.started_at)).days),
        "strategy_version":run.strategy_version,"strategy_config_hash":run.strategy_config_hash,"provider":run.provider,
        "operation_mode":config.operation_mode,"market_status":"MARKET OPEN" if session.is_open(now) else "MARKET CLOSED",
        "completed_scans":sum(item.completed_at is not None for item in scans),"signals":sum(item.signals for item in scans),
        "trades":db.scalar(select(func.count()).select_from(Trade).where(Trade.run_id==run.run_id)) or 0,
        "trades_today":trades_today,"watchlist_count":watchlist_count,"daily_pnl":daily.daily_pnl if daily else Decimal(0),"portfolio":summary,
        "scanner":{"last_scan":last.completed_at if last else None,"last_processed_candle":last.closed_candle_timestamp if last else None,
            "next_expected_candle":last_closed+timedelta(minutes=15) if last_closed else None,"symbols_scanned":last.total_symbols if last else 0,
            "valid":last.valid_symbols if last else 0,"failed":last.failed_symbols if last else 0,
            "watchlist":last.watchlist_count if last else 0,"signals":last.signals if last else 0,"orders":last.entries if last else 0},
        "performance":performance,
        "worker":{"embedded":config.embedded_worker_enabled,"always_on":True,
            "current_cadence_minutes":5 if session.is_open(now) else 15,
            "position_check_minutes":5,"strategy_candle_minutes":15},
        "benchmark":{"symbol":"XU100","start_price":run.benchmark_start_price,"latest_price":run.benchmark_latest_price,
            "return_pct":performance["benchmark_return_pct"],"updated_at":run.benchmark_updated_at}})


@router.post("/forward/control")
def forward_control(payload:PaperTradingControl,db:Session=Depends(get_db)):
    run=set_paused(db,config,payload.paused);return {"status":"PAUSED" if run.paused else "RUNNING","run_id":run.run_id}


@router.get("/forward/daily-summaries")
def daily_summaries(db:Session=Depends(get_db)):
    run=current_run(db);return dump(db.scalars(select(DailySummary).where(DailySummary.run_id==run.run_id).order_by(DailySummary.date)).all())


@router.get("/portfolio/history")
def portfolio_history(db: Session = Depends(get_db)):
    run=current_run(db);return dump(db.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.run_id==run.run_id).order_by(PortfolioSnapshot.timestamp)).all())


@router.get("/positions")
def positions(db: Session = Depends(get_db)):
    run=current_run(db);return dump(db.scalars(select(Position).where(Position.status == "OPEN",Position.run_id==run.run_id).order_by(desc(Position.opened_at))).all())


@router.get("/trades")
def trades(db: Session = Depends(get_db)):
    run=current_run(db);return dump(db.scalars(select(Trade).where(Trade.run_id==run.run_id).order_by(desc(Trade.exit_time))).all())


@router.get("/watchlist")
def watchlist(db: Session = Depends(get_db)):
    run=current_run(db);return dump(db.scalars(select(WatchlistItem).where(WatchlistItem.run_id==run.run_id).order_by(desc(WatchlistItem.score))).all())


@router.get("/scanner/results")
def scanner_results(db: Session = Depends(get_db)):
    # Portable latest-per-symbol result without backend-specific DISTINCT ON.
    run=current_run(db);rows = db.scalars(select(Analysis).where(Analysis.run_id==run.run_id).order_by(desc(Analysis.analyzed_at)).limit(500)).all()
    seen, result = set(), []
    for row in rows:
        if row.symbol not in seen: seen.add(row.symbol); result.append(row)
    return dump(sorted(result, key=lambda x: x.score, reverse=True))


@router.get("/scanner/status")
def scanner_status(db: Session = Depends(get_db)):
    run=current_run(db);session=BistMarketSession.from_config(config);now=datetime.now(timezone.utc)
    scans=list(db.scalars(select(ScanRun).where(ScanRun.run_id==run.run_id).order_by(desc(ScanRun.started_at)).limit(10)).all())
    last=scans[0] if scans else None
    analyses_count=db.scalar(select(func.count()).select_from(Analysis).where(Analysis.run_id==run.run_id)) or 0
    watch_count=db.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.run_id==run.run_id)) or 0
    latest_rows=db.scalars(select(Analysis).where(Analysis.run_id==run.run_id).order_by(desc(Analysis.analyzed_at)).limit(500)).all()
    latest_symbols=len({row.symbol for row in latest_rows})
    if last is None:status="NO_SCAN"
    elif last.completed_at is None:status="RUNNING"
    elif last.failed_symbols:status="COMPLETE_WITH_ERRORS"
    else:status="COMPLETE"
    last_closed=expected_closed_candle(config,now)
    market_open=session.is_open(now);analysis_mode="LIVE" if market_open else "ANALYSIS_ONLY"
    last_market_candle=db.scalar(select(func.max(Analysis.signal_candle_time)).where(Analysis.run_id==run.run_id))
    last_off_hours=next((item for item in scans if item.analysis_mode=="ANALYSIS_ONLY"),None)
    next_off_hours=(last_off_hours.started_at+timedelta(minutes=config.off_hours_scan_interval_minutes)
        if last_off_hours and last_off_hours.started_at else now) if config.off_hours_scan_enabled and not market_open else None
    last_funnel=last.funnel or {} if last else {}
    score_stats={"highest":last_funnel.get("score_highest",max((row.score for row in latest_rows),default=0)),
        "average":last_funnel.get("score_average",round(sum(row.score for row in latest_rows)/len(latest_rows),2) if latest_rows else 0),
        "above_watchlist":last_funnel.get("score_above_watchlist",sum(row.score>=config.watchlist_score for row in latest_rows)),
        "above_entry":last_funnel.get("score_above_entry",sum(row.score>=config.entry_score for row in latest_rows))}
    health_rows=db.scalars(select(SymbolHealth).where(SymbolHealth.consecutive_failures>0)
        .order_by(desc(SymbolHealth.last_failure_at)).limit(20)).all()
    return dump({
        "status":status,
        "market_status":"MARKET OPEN" if market_open else "MARKET CLOSED",
        "analysis_mode":analysis_mode,"entries_enabled":market_open and analysis_mode=="LIVE",
        "last_market_candle":last_market_candle,"off_hours_scan_enabled":config.off_hours_scan_enabled,
        "off_hours_scan_interval_minutes":config.off_hours_scan_interval_minutes,
        "off_hours_scan_symbol_limit":config.off_hours_scan_symbol_limit,"next_off_hours_scan":next_off_hours,
        "provider":config.market_data_provider,
        "auto_worker":config.embedded_worker_enabled,
        "scanner_symbol_limit":config.scanner_symbol_limit,
        "manual_scan_symbol_limit":config.manual_scan_symbol_limit,
        "watchlist_score":config.watchlist_score,
        "entry_score":config.entry_score,
        "next_automatic_scan":last_closed+timedelta(minutes=15) if last_closed and session.is_open(now) else None,
        "watchlist_count":watch_count,
        "latest_analysis_symbols":latest_symbols,
        "analysis_rows":analyses_count,
        "score_stats":score_stats,
        "symbol_health":[{"symbol":row.symbol,"type":row.last_error_type or "UNKNOWN",
            "status":"UNIVERSE_DATA_UNAVAILABLE" if row.last_error_type in {"SYMBOL_NOT_FOUND","INSUFFICIENT_HISTORY"} else "PROVIDER_ERROR",
            "message":row.last_error_message or "","error":row.last_error_message or "",
            "consecutive_failures":row.consecutive_failures,"retry_at":row.quarantined_until,
            "last_failure_at":row.last_failure_at} for row in health_rows],
        "last_scan":None if last is None else {
            "started_at":last.started_at,"completed_at":last.completed_at,"duration_ms":last.duration_ms,
            "total_symbols":last.total_symbols,"valid_symbols":last.valid_symbols,"failed_symbols":last.failed_symbols,
            "stale_symbols":last.stale_symbols,"watchlist_count":last.watchlist_count,"signals":last.signals,
            "entries":last.entries,"errors":last.errors[:20] if last.errors else [],
            "funnel":last.funnel or {},"analysis_mode":last.analysis_mode,"market_open":last.market_open,
            "source_candle_timestamp":last.source_candle_timestamp,
        },
        "recent_scans":[{
            "id":item.id,"started_at":item.started_at,"completed_at":item.completed_at,
            "total_symbols":item.total_symbols,"valid_symbols":item.valid_symbols,
            "failed_symbols":item.failed_symbols,"watchlist_count":item.watchlist_count,
            "signals":item.signals,"entries":item.entries
            ,"analysis_mode":item.analysis_mode
        } for item in scans]
    })


@router.get("/analysis/{symbol}")
def analysis(symbol: str, db: Session = Depends(get_db)):
    run=current_run(db);row = db.scalar(select(Analysis).where(Analysis.symbol == symbol.upper(),Analysis.run_id==run.run_id).order_by(desc(Analysis.analyzed_at)).limit(1))
    if not row: raise HTTPException(404, "Analiz bulunamadı")
    return dump(row)


@router.get("/news/health")
def news_health(db: Session = Depends(get_db)):
    return dump(NewsService(db,config).health())


@router.get("/news/metrics")
def news_metrics(db:Session=Depends(get_db)):
    return dump(NewsService(db,config).metrics())


@router.get("/news/sources/health")
def news_sources_health(db:Session=Depends(get_db)):
    return dump(NewsService(db,config).sources_health())


@router.get("/news/reactions/status")
def news_reaction_status(db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).reaction_status())


@router.get("/news/reactions/recent")
def recent_news_reactions(limit:int=Query(100,ge=1,le=500),db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).recent_reactions(limit))


@router.get("/news/important")
def important_news(min_importance:int=Query(80,ge=0,le=100),limit:int=Query(100,ge=1,le=500),db:Session=Depends(get_db)):
    return dump(NewsService(db,config).list(min_importance=min_importance,limit=limit))


@router.get("/news/archive")
def news_archive(symbol:str|None=None,source:str|None=None,category:str|None=None,sentiment:str|None=None,
    min_importance:int|None=Query(None,ge=0,le=100),start:datetime|None=Query(None,alias="start_date"),
    end:datetime|None=Query(None,alias="end_date"),
    overnight_only:bool=False,reaction_only:bool=False,reaction_complete_only:bool=False,
    limit:int=Query(500,ge=1,le=2000),db:Session=Depends(get_db)):
    return dump(NewsService(db,config).archive(symbol,source,category,sentiment,min_importance,
        start,end,overnight_only,reaction_only,limit,reaction_complete_only))


@router.get("/news/linked-symbols")
def linked_news_symbols(db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).linked_news_symbols())

@router.get("/news/unmatched")
def unmatched_news(limit:int=Query(100,ge=1,le=500),db:Session=Depends(get_db)):
    return dump(NewsService(db,config).unmatched(limit))

@router.post("/news/reconcile-symbols")
def reconcile_news_symbols(batch_size:int=Query(50,ge=1,le=100),db:Session=Depends(get_db)):
    return dump(NewsSymbolReconciliationService(db,config).run(batch_size))


@router.get("/news/reactions/{symbol}")
def news_reactions(symbol:str,limit:int=Query(500,ge=1,le=2000),db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).reactions(symbol,limit))


@router.get("/news/event-study")
def news_event_study(db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).event_study())


@router.get("/news")
def news(source:str|None=None,sentiment:str|None=None,min_importance:int|None=Query(None,ge=0,le=100),limit:int=Query(100,ge=1,le=500),db:Session=Depends(get_db)):
    return dump(NewsService(db,config).list(source=source,sentiment=sentiment,min_importance=min_importance,limit=limit))


@router.get("/news/{symbol}/latest")
def latest_news(symbol:str,db:Session=Depends(get_db)):
    rows=NewsService(db,config).list(symbol=symbol,limit=1)
    if not rows:raise HTTPException(404,"Haber bulunamadı")
    return dump(rows[0])


@router.get("/news/{symbol}")
def symbol_news(symbol:str,limit:int=Query(100,ge=1,le=500),db:Session=Depends(get_db)):
    return dump(NewsService(db,config).list(symbol=symbol,limit=limit))


@router.post("/news/refresh")
def refresh_news(db:Session=Depends(get_db)):
    return dump(NewsService(db,config).refresh())


@router.get("/market-history/{symbol}/snapshot")
def market_snapshot(symbol:str,at:datetime,db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).snapshot_at(symbol,at))


@router.get("/market-history/{symbol}/snapshot-detail")
def market_snapshot_detail(symbol:str,at:datetime,db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).snapshot_detail(symbol,at))


@router.get("/market-history/{symbol}/trend")
def market_trend(symbol:str,start:datetime|None=None,end:datetime|None=None,
    limit:int=Query(1000,ge=1,le=5000),db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).trend(symbol,start,end,limit))


@router.get("/market-history/{symbol}/news")
def market_news(symbol:str,start:datetime|None=None,end:datetime|None=None,
    limit:int=Query(500,ge=1,le=2000),db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).news(symbol,start,end,limit))


@router.get("/market-history/{symbol}/opening-context")
def opening_context(symbol:str,at:datetime|None=None,db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).opening_context(symbol,at))


@router.get("/market-history/{symbol}")
def market_history(symbol:str,start:datetime|None=None,end:datetime|None=None,
    limit:int=Query(500,ge=1,le=5000),db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).history(symbol,start,end,limit))


@router.get("/market-memory/health")
def market_memory_health(db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).health())


@router.get("/market-memory/symbols")
def market_memory_symbols(db:Session=Depends(get_db)):
    return dump(MarketMemoryService(db,config).memory_symbols())


@router.get("/backfill/status")
def backfill_status(db:Session=Depends(get_db)):
    return dump(BackfillService(db,config).status())


@router.get("/data-collection/activity")
def data_collection_activity(limit:int=Query(100,ge=1,le=500),db:Session=Depends(get_db)):
    return dump(recent_activity(db,limit))


@router.post("/backfill/run")
def run_backfill(batch_size:int|None=Query(None,ge=1,le=5),db:Session=Depends(get_db)):
    return dump(BackfillService(db,config).run(batch_size))


@router.get("/candles/{symbol}")
def candles(symbol: str, timeframe: str = Query("15m", pattern="^(5m|15m|1h|1d)$"),
    limit: int = Query(200, ge=1, le=1000),at:datetime|None=None,start:datetime|None=None,
    end:datetime|None=None,db_only:bool=False,db: Session = Depends(get_db)):
    if start and end and start>end:raise HTTPException(422,"start, end değerinden sonra olamaz")
    stmt=select(Candle).where(Candle.symbol == symbol.upper(), Candle.timeframe == timeframe)
    if start:stmt=stmt.where(Candle.timestamp>=start)
    if end:stmt=stmt.where(Candle.timestamp<=end)
    if at:stmt=stmt.where(Candle.timestamp<=at)
    rows = db.scalars(stmt.order_by(desc(Candle.timestamp)).limit(limit)).all()
    if timeframe=="5m" and at is None and not db_only and len(rows)<min(limit,35):
        try:
            fetched=BistScanner(db,config).provider.get_candles(symbol.upper(),timeframe,max(limit,220))
            existing={row.timestamp for row in rows}
            for candle in fetched:
                if candle.timestamp not in existing:db.add(Candle(symbol=symbol.upper(),timeframe=timeframe,timestamp=candle.timestamp,
                    open=candle.open,high=candle.high,low=candle.low,close=candle.close,volume=candle.volume,source="hybrid"))
            db.commit();rows=db.scalars(select(Candle).where(Candle.symbol==symbol.upper(),Candle.timeframe==timeframe).order_by(desc(Candle.timestamp)).limit(limit)).all()
        except Exception as exc:
            if not rows:raise HTTPException(503,f"5M veri alınamadı: {type(exc).__name__}") from None
    ordered=list(reversed(rows));series=indicator_series(ordered)
    return dump([{**{key:getattr(row,key) for key in ("timestamp","open","high","low","close","volume","source")},
        "indicators":series[index]} for index,row in enumerate(ordered)])


@router.get("/decisions")
def decisions(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    run=current_run(db);return dump(db.scalars(select(DecisionLog).where(DecisionLog.run_id==run.run_id).order_by(desc(DecisionLog.created_at)).limit(limit)).all())


@router.post("/scanner/run")
def scanner_run(max_symbols: int | None = Query(None, ge=1, le=100),db: Session = Depends(get_db),mode:str|None=Query(None,pattern="^(analysis_only|live)$")):
    market_open=BistMarketSession.from_config(config).is_open()
    analysis_mode="ANALYSIS_ONLY" if mode=="analysis_only" or not market_open else "LIVE"
    limit=min(max_symbols or config.manual_scan_symbol_limit,config.scanner_symbol_limit)
    return BistScanner(db,config,require_market_session=analysis_mode=="LIVE",analysis_mode=analysis_mode,
        market_open=market_open).run(limit)


@router.get("/settings")
def settings(db: Session = Depends(get_db)): return dump(effective_settings(db, config))


@router.post("/settings")
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    changes=payload.model_dump(exclude_none=True);run=active_forward_run(db)
    critical={"entry_score","risk_per_trade_pct","max_position_size_pct","minimum_cash_reserve_pct",
        "max_open_positions","min_rr","commission_rate","slippage_rate"}
    current=effective_settings(db,config)
    changed_critical=[key for key,value in changes.items() if key in critical and str(value)!=str(current[key])]
    if run and changed_critical:
        raise HTTPException(409,{"code":"STRATEGY_VERSION_REQUIRED","message":"Aktif forward test sırasında kritik strateji ayarları değiştirilemez.",
            "fields":changed_critical,"strategy_version":run.strategy_version})
    for key, value in changes.items():
        row = db.get(Setting, key)
        encoded = {"value": str(value) if isinstance(value, Decimal) else value}
        if row: row.value = encoded
        else: db.add(Setting(key=key, value=encoded))
    db.commit(); return settings(db)


@router.post("/portfolio/reset")
def reset_portfolio(payload: PortfolioReset, db: Session = Depends(get_db)):
    if payload.confirmation != "RESET PAPER PORTFOLIO": raise HTTPException(400, "Onay metni hatalı")
    previous=active_forward_run(db);run=reset_forward_run(db,config)
    return {"status":"reset","initial_balance":float(config.initial_balance),"archived_run_id":previous.run_id if previous else None,
        "run_id":run.run_id}


@router.get("/forward/runs")
def forward_runs(db:Session=Depends(get_db)):
    return dump(db.scalars(select(ForwardRun).order_by(desc(ForwardRun.started_at))).all())


@router.get("/forward/runs/{run_id}/trades")
def archived_run_trades(run_id:str,db:Session=Depends(get_db)):
    if not db.get(ForwardRun,run_id):raise HTTPException(404,"Forward run bulunamadı")
    return dump(db.scalars(select(Trade).where(Trade.run_id==run_id).order_by(desc(Trade.exit_time))).all())
