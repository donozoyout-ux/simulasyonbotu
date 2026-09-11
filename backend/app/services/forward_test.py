from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DailySummary, ForwardRun, Portfolio, PortfolioSnapshot, Position, ScanRun, Trade
from app.market_data.market_session import BistMarketSession
from app.portfolio.portfolio_manager import ensure_portfolio, portfolio_summary, take_snapshot
from app.research.v4 import strategy_config_snapshot


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def active_forward_run(db: Session) -> ForwardRun | None:
    return db.scalar(select(ForwardRun).where(ForwardRun.status=="RUNNING").order_by(ForwardRun.started_at.desc()).limit(1))


def _next_run_id(db: Session, now: datetime) -> str:
    prefix=f"LIVE-{now.astimezone(timezone.utc).strftime('%Y%m%d')}-"
    count=db.scalar(select(func.count()).select_from(ForwardRun).where(ForwardRun.run_id.like(f"{prefix}%"))) or 0
    return f"{prefix}{count+1:03d}"


def ensure_forward_run(db: Session, config, now: datetime | None=None) -> ForwardRun:
    current=active_forward_run(db)
    if current:
        if current.provider!=config.market_data_provider:current.provider=config.market_data_provider;db.commit();db.refresh(current)
        return current
    now=as_utc(now) or datetime.now(timezone.utc);snapshot=strategy_config_snapshot(config)
    run=ForwardRun(run_id=_next_run_id(db,now),started_at=now,status="RUNNING",
        strategy_version=config.live_strategy_version,strategy_config_hash=snapshot["sha256"],
        provider=config.market_data_provider,initial_balance=config.initial_balance,paused=False)
    db.add(run);db.commit();db.refresh(run)
    portfolio=ensure_portfolio(db,config.initial_balance)
    portfolio.initial_balance=config.initial_balance
    if not db.scalar(select(PortfolioSnapshot.id).where(PortfolioSnapshot.run_id==run.run_id).limit(1)):
        take_snapshot(db,config.initial_balance,run.run_id)
    return run


def set_paused(db:Session,config,paused:bool)->ForwardRun:
    run=ensure_forward_run(db,config);run.paused=paused;db.commit();db.refresh(run);return run


def reset_forward_run(db:Session,config,now:datetime|None=None)->ForwardRun:
    current=active_forward_run(db);now=as_utc(now) or datetime.now(timezone.utc)
    if current:
        current.status="ARCHIVED";current.ended_at=now
        for position in db.scalars(select(Position).where(Position.status=="OPEN",Position.run_id==current.run_id)):
            position.status="ARCHIVED"
    portfolio=ensure_portfolio(db,config.initial_balance)
    portfolio.initial_balance=config.initial_balance;portfolio.cash_balance=config.initial_balance
    portfolio.realized_pnl=Decimal(0);portfolio.updated_at=now;db.commit()
    return ensure_forward_run(db,config,now)


def forward_performance(db:Session,config,run:ForwardRun)->dict:
    trades=list(db.scalars(select(Trade).where(Trade.run_id==run.run_id)).all())
    snapshots=list(db.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.run_id==run.run_id)
                              .order_by(PortfolioSnapshot.timestamp)).all())
    wins=[trade.realized_pnl for trade in trades if trade.realized_pnl>0]
    losses=[trade.realized_pnl for trade in trades if trade.realized_pnl<=0]
    peak=run.initial_balance;max_drawdown=Decimal(0);exposures=[]
    for item in snapshots:
        peak=max(peak,item.portfolio_value)
        if peak:max_drawdown=max(max_drawdown,(peak-item.portfolio_value)/peak*100)
        if item.portfolio_value:exposures.append(float(item.invested_value/item.portfolio_value*100))
    holding=[(as_utc(trade.exit_time)-as_utc(trade.entry_time)).total_seconds()/3600 for trade in trades]
    benchmark_return=None
    if run.benchmark_start_price and run.benchmark_latest_price:
        benchmark_return=float((run.benchmark_latest_price/run.benchmark_start_price-1)*100)
    return {"trades":len(trades),"wins":len(wins),"losses":len(losses),
        "win_rate":round(len(wins)/len(trades)*100,2) if trades else 0,
        "profit_factor":round(float(sum(wins)/abs(sum(losses))),4) if losses and sum(losses) else None,
        "average_win":round(float(mean(wins)),2) if wins else 0,"average_loss":round(float(mean(losses)),2) if losses else 0,
        "max_drawdown_pct":round(float(max_drawdown),4),"fees":float(sum((trade.fees for trade in trades),Decimal(0))),
        "slippage":float(sum((trade.slippage_cost for trade in trades),Decimal(0))),
        "average_holding_hours":round(mean(holding),2) if holding else 0,
        "average_exposure_pct":round(mean(exposures),2) if exposures else 0,
        "sample_warning":"LOW SAMPLE" if len(trades)<30 else None,"benchmark_return_pct":benchmark_return}


def upsert_daily_summary(db:Session,config,run:ForwardRun,now:datetime|None=None)->DailySummary:
    now=as_utc(now) or datetime.now(timezone.utc);session=BistMarketSession.from_config(config)
    day=now.astimezone(session.tz).date().isoformat()
    snapshots=list(db.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.run_id==run.run_id)
        .order_by(PortfolioSnapshot.timestamp)).all())
    todays=[row for row in snapshots if as_utc(row.timestamp).astimezone(session.tz).date().isoformat()==day]
    summary=portfolio_summary(db,config.initial_balance)
    starting=todays[0].portfolio_value if todays else summary["portfolio_value"]
    trades=list(db.scalars(select(Trade).where(Trade.run_id==run.run_id)).all())
    todays_trades=[row for row in trades if as_utc(row.exit_time).astimezone(session.tz).date().isoformat()==day]
    positions=list(db.scalars(select(Position).where(Position.run_id==run.run_id,Position.status=="OPEN")).all())
    row=db.scalar(select(DailySummary).where(DailySummary.run_id==run.run_id,DailySummary.date==day))
    values={"starting_equity":starting,"ending_equity":summary["portfolio_value"],
        "daily_pnl":summary["portfolio_value"]-starting,
        "daily_return_pct":(summary["portfolio_value"]-starting)/starting*100 if starting else Decimal(0),
        "trades":len(todays_trades),"wins":sum(item.realized_pnl>0 for item in todays_trades),
        "losses":sum(item.realized_pnl<=0 for item in todays_trades),
        "fees":sum((item.fees for item in todays_trades),Decimal(0)),
        "largest_position":max((item.current_price*item.quantity for item in positions),default=Decimal(0)),
        "cash_pct":summary["cash_balance"]/summary["portfolio_value"]*100 if summary["portfolio_value"] else Decimal(0)}
    if row:
        for key,value in values.items():setattr(row,key,value)
    else:
        row=DailySummary(run_id=run.run_id,date=day,**values);db.add(row)
    db.commit();db.refresh(row);return row
