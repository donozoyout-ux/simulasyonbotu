from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.pipeline import analyze_frames
from app.market_data.market_session import BistMarketSession, TIMEFRAME_DELTA
from app.models import Portfolio,Position,Trade
from app.portfolio.paper_broker import DuplicateOrderError,PaperBroker
from app.portfolio.portfolio_manager import ensure_portfolio,portfolio_summary
from app.portfolio.risk_manager import size_position


class ReplayEngine:
    """Walk-forward replay using the exact live analysis pipeline and broker."""
    def __init__(self,db:Session,config):
        self.db,self.config=db,config

    def run(self,symbol:str,frames:dict,step:int=4)->dict:
        portfolio=ensure_portfolio(self.db,self.config.initial_balance)
        broker=PaperBroker(self.db,self.config.commission_rate,self.config.slippage_rate,self.config.intrabar_policy)
        stats={"signals":0,"trades":0,"funnel":{"timestamps":0,"analysis_complete":0,"setup":0,"score":0,"rr":0,"risk":0,"buy":0},"max_visible_timestamp":None}
        entry_candles=frames["15m"]
        session=BistMarketSession.from_config(self.config)
        for index in range(60,len(entry_candles),step):
            current=entry_candles[index]
            analysis_at=current.timestamp+TIMEFRAME_DELTA["15m"]
            visible={tf:[c for c in rows if session.candle_is_closed(c.timestamp,tf,analysis_at)] for tf,rows in frames.items()}
            if any(len(visible[tf])<60 for tf in visible):continue
            stats["funnel"]["timestamps"]+=1
            for position in list(self.db.scalars(select(Position).where(Position.status=="OPEN"))):
                broker.evaluate_candle(portfolio,position,current)
            result=analyze_frames(symbol,visible,self.config.model_copy(update={"data_mode":"replay"}),"replay",analysis_at,False)
            stats["max_visible_timestamp"]=max(c.timestamp for rows in visible.values() for c in rows).isoformat()
            if result.details["analysis_complete"]:stats["funnel"]["analysis_complete"]+=1
            if result.funnel["valid_setup"]:stats["funnel"]["setup"]+=1
            if result.funnel["score_pass"]:stats["funnel"]["score"]+=1
            if result.funnel["rr_pass"]:stats["funnel"]["rr"]+=1
            if result.decision!="POSSIBLE_ENTRY":continue
            stats["signals"]+=1
            if self.db.scalar(select(Position.id).where(Position.symbol==symbol,Position.status=="OPEN")):continue
            setup=result.details["setup"];summary=portfolio_summary(self.db,self.config.initial_balance)
            risk=size_position(summary["portfolio_value"],summary["cash_balance"],result.price,setup["invalidation_level"],setup["target"],
                self.config.risk_per_trade_pct,self.config.max_position_size_pct,self.config.minimum_cash_reserve_pct,summary["open_positions"],
                self.config.max_open_positions,self.config.min_rr,result.details["volatility"]["atr"],self.config.minimum_stop_atr_pct,self.config.maximum_stop_atr_pct)
            if not risk.approved:continue
            stats["funnel"]["risk"]+=1
            key=f"replay:{symbol}:{result.setup}:{result.signal_candle_time.isoformat()}"
            try:
                broker.buy(portfolio,symbol,risk.quantity,result.price,setup["invalidation_level"],setup["target"],result.setup,result.score,result.reason,key,result.signal_candle_time)
                stats["funnel"]["buy"]+=1
            except DuplicateOrderError:pass
        last=entry_candles[-1]
        for position in list(self.db.scalars(select(Position).where(Position.status=="OPEN"))):
            broker.sell(portfolio,position,position.quantity,last.close,"Replay period end","MANUAL_CLOSE")
        trades=list(self.db.scalars(select(Trade)).all());summary=portfolio_summary(self.db,self.config.initial_balance)
        stats.update({"trades":len(trades),"winners":sum(t.realized_pnl>0 for t in trades),"losers":sum(t.realized_pnl<=0 for t in trades),
            "ending_portfolio":summary["portfolio_value"],"total_pnl":summary["total_pnl"],"fees":sum((t.commission for t in trades),Decimal(0)),
            "slippage_cost":sum((t.slippage_cost for t in trades),Decimal(0)),"period":{"start":entry_candles[60].timestamp.isoformat(),"end":last.timestamp.isoformat()},"symbol":symbol})
        return stats
