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
from app.db.session import get_db
from app.models import Analysis, Candle, DailySummary, DecisionLog, ForwardRun, Portfolio, PortfolioSnapshot, Position, ScanRun, Setting, Trade, WatchlistItem
from app.market_data.market_session import BistMarketSession
from app.portfolio.portfolio_manager import ensure_portfolio, portfolio_summary, take_snapshot
from app.schemas.common import PaperTradingControl, PortfolioReset, SettingsUpdate
from app.scanner.bist_scanner import BistScanner, effective_settings
from app.services.forward_test import active_forward_run,ensure_forward_run,forward_performance,reset_forward_run,set_paused
from app.services.forward_worker import expected_closed_candle

router = APIRouter()
config = get_settings()
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def dump(value):
    return jsonable_encoder(value, custom_encoder={Decimal: lambda x: float(x)})


def current_run(db:Session)->ForwardRun:
    return ensure_forward_run(db,config)


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1)); return {"status": "healthy", "mode": config.data_mode.upper(), "provider": config.market_data_provider,
        "real_orders": False,"ai":ai_health(config)}


@router.get("/ai/status")
def ai_status():
    return ai_health(config)


@router.get("/data-health")
def data_health(db: Session = Depends(get_db)):
    forward=active_forward_run(db)
    run=db.scalar(select(ScanRun).where(ScanRun.run_id==forward.run_id).order_by(desc(ScanRun.started_at)).limit(1)) if forward else None
    if not run:return {"provider":config.market_data_provider,"mode":config.data_mode.upper(),"status":"NO_SCAN","valid_symbols":0,"failed_symbols":0,"stale_symbols":0,"errors":[]}
    return dump({"provider":run.provider,"mode":run.data_mode.upper(),"status":"DATA_ERROR" if run.failed_symbols else "OK",
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


@router.get("/analysis/{symbol}")
def analysis(symbol: str, db: Session = Depends(get_db)):
    run=current_run(db);row = db.scalar(select(Analysis).where(Analysis.symbol == symbol.upper(),Analysis.run_id==run.run_id).order_by(desc(Analysis.analyzed_at)).limit(1))
    if not row: raise HTTPException(404, "Analiz bulunamadı")
    return dump(row)


@router.get("/candles/{symbol}")
def candles(symbol: str, timeframe: str = Query("15m", pattern="^(15m|1h|1d)$"), limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    rows = db.scalars(select(Candle).where(Candle.symbol == symbol.upper(), Candle.timeframe == timeframe).order_by(desc(Candle.timestamp)).limit(limit)).all()
    return dump(list(reversed(rows)))


@router.get("/decisions")
def decisions(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    run=current_run(db);return dump(db.scalars(select(DecisionLog).where(DecisionLog.run_id==run.run_id).order_by(desc(DecisionLog.created_at)).limit(limit)).all())


@router.post("/scanner/run")
def scanner_run(max_symbols: int | None = Query(None, ge=1, le=100), db: Session = Depends(get_db)):
    if config.data_mode=="live" and not BistMarketSession.from_config(config).is_open():
        return {"status":"market_closed","analyzed":0,"errors":[],"results":[]}
    return BistScanner(db, config).run(max_symbols)


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
