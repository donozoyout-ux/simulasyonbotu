from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import timezone
from decimal import Decimal
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.pipeline import analyze_frames
from app.market_data.market_session import BistMarketSession, TIMEFRAME_DELTA
from app.models import Position, Trade
from app.portfolio.paper_broker import DuplicateOrderError, PaperBroker
from app.portfolio.portfolio_manager import ensure_portfolio, portfolio_summary
from app.portfolio.risk_manager import size_position
from app.research.v4 import percent_from_fraction, position_size_diagnostics, strategy_config_snapshot


class PortfolioReplayEngine:
    """Chronological, shared-cash, multi-symbol replay using the live pipeline and paper broker."""
    def __init__(self, db: Session, config):
        self.db,self.config=db,config

    def run(self, frames_by_symbol:dict, benchmark:list|None=None, analysis_step:int=4,
            evaluation_start=None, evaluation_end=None)->dict:
        config_snapshot=strategy_config_snapshot(self.config)
        portfolio=ensure_portfolio(self.db,self.config.initial_balance)
        broker=PaperBroker(self.db,self.config.commission_rate,self.config.slippage_rate,self.config.intrabar_policy)
        session=BistMarketSession.from_config(self.config)
        time_arrays={symbol:{tf:[item.timestamp for item in rows] for tf,rows in frames.items()} for symbol,frames in frames_by_symbol.items()}
        trigger_maps={symbol:{item.timestamp:item for item in frames["15m"]} for symbol,frames in frames_by_symbol.items()}
        events=sorted({stamp for mapping in trigger_maps.values() for stamp in mapping})
        if evaluation_start is None and evaluation_end is None:
            events=events[80:]
        else:
            events=[stamp for stamp in events
                    if (evaluation_start is None or stamp>=evaluation_start)
                    and (evaluation_end is None or stamp<=evaluation_end)]
        funnel=Counter();rejections=Counter();capital_diagnostics=[];equity_curve=[];excursions={};exposure_samples=[];allocation_timeline=[]
        for event_index,event_time in enumerate(events):
            analysis_at=event_time+TIMEFRAME_DELTA["15m"]
            open_positions=list(self.db.scalars(select(Position).where(Position.status=="OPEN")))
            for position in open_positions:
                current=trigger_maps.get(position.symbol,{}).get(event_time)
                if not current:continue
                tracker=excursions.setdefault(position.id,{"mfe":Decimal(0),"mae":Decimal(0)})
                tracker["mfe"]=max(tracker["mfe"],(current.high/position.entry_price-1)*100)
                tracker["mae"]=min(tracker["mae"],(current.low/position.entry_price-1)*100)
                broker.evaluate_candle(portfolio,position,current,analysis_at)
            candidates=[]
            if event_index%analysis_step==0:
                for symbol,frames in frames_by_symbol.items():
                    if event_time not in trigger_maps[symbol]:continue
                    visible={}
                    for tf,rows in frames.items():
                        end=bisect_right(time_arrays[symbol][tf],analysis_at)
                        visible[tf]=[item for item in rows[max(0,end-260):end] if session.candle_is_closed(item.timestamp,tf,analysis_at)]
                    if any(len(visible[tf])<60 for tf in visible):continue
                    funnel["analysis_complete_evaluated"]+=1
                    result=analyze_frames(symbol,visible,self.config.model_copy(update={"data_mode":"replay"}),"replay",analysis_at,False)
                    if not result.details["analysis_complete"]:rejections["analysis_incomplete"]+=1;continue
                    funnel["analysis_complete"]+=1
                    if result.trend=="bearish":rejections["htf_bearish"]+=1;continue
                    funnel["htf_eligible"]+=1
                    if not result.funnel["valid_setup"]:rejections["setup_missing"]+=1;continue
                    funnel["valid_setup"]+=1
                    if result.score<self.config.entry_score:rejections["score_below_threshold"]+=1;continue
                    funnel["score_pass"]+=1
                    if not result.funnel["rr_pass"]:rejections["rr_below_minimum"]+=1;continue
                    funnel["rr_pass"]+=1
                    candidates.append(result)
            for result in sorted(candidates,key=lambda item:(item.score,item.details["setup"]["score"]),reverse=True):
                funnel["signals"]+=1
                if self.db.scalar(select(Position.id).where(Position.symbol==result.symbol,Position.status=="OPEN")):
                    rejections["existing_position"]+=1;continue
                setup=result.details["setup"];summary=portfolio_summary(self.db,self.config.initial_balance)
                entry,stop,target=result.price,Decimal(str(setup["invalidation_level"])),Decimal(str(setup["target"]))
                risk_amount=summary["portfolio_value"]*self.config.risk_per_trade_pct
                ideal=int(risk_amount/(entry-stop)) if entry>stop else 0
                affordable=int(max(Decimal(0),summary["cash_balance"]-summary["portfolio_value"]*self.config.minimum_cash_reserve_pct)/entry)
                risk=size_position(summary["portfolio_value"],summary["cash_balance"],entry,stop,target,self.config.risk_per_trade_pct,
                    self.config.max_position_size_pct,self.config.minimum_cash_reserve_pct,summary["open_positions"],self.config.max_open_positions,
                    self.config.min_rr,result.details["volatility"]["atr"],self.config.minimum_stop_atr_pct,self.config.maximum_stop_atr_pct)
                sizing=position_size_diagnostics(summary["portfolio_value"],summary["cash_balance"],entry,stop,
                    self.config.risk_per_trade_pct,self.config.max_position_size_pct,self.config.minimum_cash_reserve_pct)
                capital_diagnostics.append({"symbol":result.symbol,"timestamp":result.signal_candle_time.isoformat(),"score":result.score,
                    "ideal_quantity":ideal,"affordable_quantity":affordable,"approved_quantity":risk.quantity,"reason":risk.reason,
                    "atr":float(result.details["volatility"]["atr"]),
                    "stop_distance_atr":float((entry-stop)/result.details["volatility"]["atr"])})
                capital_diagnostics[-1].update(sizing)
                if not risk.approved:
                    rejections["risk_rejected"]+=1
                    if affordable<1 or "Sermaye" in risk.reason:rejections["capital_rejected"]+=1
                    continue
                funnel["risk_pass"]+=1
                try:
                    position=broker.buy(portfolio,result.symbol,risk.quantity,entry,stop,target,result.setup,result.score,result.reason,
                        f"portfolio-replay:{result.symbol}:{result.setup}:{result.signal_candle_time.isoformat()}",result.signal_candle_time,analysis_at)
                    excursions[position.id]={"mfe":Decimal(0),"mae":Decimal(0)};funnel["entries"]+=1
                except DuplicateOrderError:rejections["duplicate"]+=1
            summary=portfolio_summary(self.db,self.config.initial_balance)
            equity_curve.append((analysis_at,summary["portfolio_value"]))
            exposure_samples.append(float(summary["invested_value"]/summary["portfolio_value"]) if summary["portfolio_value"] else 0)
            allocation_timeline.append({"timestamp":analysis_at.isoformat(),"cash":float(summary["cash_balance"]),
                "market_value":float(summary["invested_value"]),"equity":float(summary["portfolio_value"]),
                "allocated_pct":round(exposure_samples[-1]*100,4),"free_cash_pct":round((1-exposure_samples[-1])*100,4)})
        if events:
            end_time=events[-1]+TIMEFRAME_DELTA["15m"]
            for position in list(self.db.scalars(select(Position).where(Position.status=="OPEN"))):
                last=max((item for stamp,item in trigger_maps[position.symbol].items() if stamp<=events[-1]),key=lambda item:item.timestamp)
                broker.sell(portfolio,position,position.quantity,last.close,"Replay period end","MANUAL_CLOSE",end_time)
        trades=list(self.db.scalars(select(Trade)).all());summary=portfolio_summary(self.db,self.config.initial_balance)
        pnls=[float(item.realized_pnl) for item in trades];wins=[value for value in pnls if value>0];losses=[value for value in pnls if value<=0]
        peak=float(self.config.initial_balance);max_drawdown=0
        for _,value in equity_curve:
            numeric=float(value);peak=max(peak,numeric);max_drawdown=max(max_drawdown,(peak-numeric)/peak*100 if peak else 0)
        start=events[0] if events else None;end=events[-1] if events else None
        benchmark_return=None
        benchmark_drawdown=None
        if benchmark and start and end:
            available=[item for item in benchmark if start<=item.timestamp<=end]
            if len(available)>=2:
                benchmark_return=float((available[-1].close/available[0].close-1)*100)
                peak=available[0].close;drawdown=Decimal(0)
                for item in available:
                    peak=max(peak,item.close);drawdown=max(drawdown,(peak-item.close)/peak*100)
                benchmark_drawdown=float(drawdown)
        trade_rows=[]
        for trade in trades:
            tracker=excursions.get(trade.position_id,{"mfe":Decimal(0),"mae":Decimal(0)})
            opened=trade.entry_time.replace(tzinfo=timezone.utc) if trade.entry_time.tzinfo is None else trade.entry_time
            closed=trade.exit_time.replace(tzinfo=timezone.utc) if trade.exit_time.tzinfo is None else trade.exit_time
            symbol_rows=frames_by_symbol[trade.symbol]["15m"];times=time_arrays[trade.symbol]["15m"]
            start_index=bisect_left(times,opened)
            marks={}
            for label,bars in (("15m",1),("30m",2),("1h",4),("2h",8),("4h",16),("1d",32)):
                index=start_index+bars-1
                marks[label]=float((symbol_rows[index].close/trade.entry_price-1)*100) if index<len(symbol_rows) else None
            one_day=symbol_rows[start_index:min(len(symbol_rows),start_index+32)]
            recovery={"entry_1h":False,"entry_4h":False,"entry_1d":False,"target_1d":False,"one_r_1d":False}
            after_stop={"1h":None,"2h":None,"4h":None,"1d":None}
            if trade.exit_reason and "stop" in trade.exit_reason.lower():
                exit_index=bisect_left(times,closed);risk_per_share=trade.entry_price-trade.stop_price
                for label,bars in (("entry_1h",4),("entry_4h",16),("entry_1d",32)):
                    window=symbol_rows[exit_index:min(len(symbol_rows),exit_index+bars)]
                    recovery[label]=any(item.high>=trade.entry_price for item in window)
                window=symbol_rows[exit_index:min(len(symbol_rows),exit_index+32)]
                recovery["target_1d"]=any(item.high>=trade.target_price for item in window)
                recovery["one_r_1d"]=any(item.high>=trade.entry_price+risk_per_share for item in window)
                for label,bars in (("1h",4),("2h",8),("4h",16),("1d",32)):
                    index=exit_index+bars-1
                    if index<len(symbol_rows):after_stop[label]=float((symbol_rows[index].close/trade.entry_price-1)*100)
            sizing=next((item for item in capital_diagnostics if item["symbol"]==trade.symbol),{})
            trade_rows.append({"symbol":trade.symbol,"setup":trade.setup_type,"pnl":float(trade.realized_pnl),"actual_exit_return":float(trade.return_pct),
                "exit_reason":trade.exit_reason,"entry":float(trade.entry_price),"stop":float(trade.stop_price),"target":float(trade.target_price),
                "stop_distance_pct":float((trade.entry_price-trade.stop_price)/trade.entry_price*100),"target_distance_pct":float((trade.target_price-trade.entry_price)/trade.entry_price*100),
                "atr":sizing.get("atr"),"stop_distance_atr":sizing.get("stop_distance_atr"),
                "mfe_before_exit":float(tracker["mfe"]),"mae_before_exit":float(tracker["mae"]),"mfe":float(tracker["mfe"]),"mae":float(tracker["mae"]),
                "hypothetical_hold":marks,"price_after_stop":after_stop,"stopped_then_recovered":recovery,
                "target_hit_within_1d":any(item.high>=trade.target_price for item in one_day),
                "holding_hours":round((closed-opened).total_seconds()/3600,2),"risk_per_share":float(trade.entry_price-trade.stop_price)})
        actual_risks=[item["actual_risk"] for item in capital_diagnostics if item["approved_quantity"]>0]
        risk_utilization=[item["risk_utilization_pct"] for item in capital_diagnostics if item["approved_quantity"]>0]
        stop_trades=[row for row in trade_rows if "stop" in row["exit_reason"].lower()]
        target_trades=[row for row in trade_rows if "target" in row["exit_reason"].lower()]
        exit_diagnostics={"median_holding_hours":round(median([row["holding_hours"] for row in trade_rows]),2) if trade_rows else 0,
            "average_mfe":round(mean([row["mfe"] for row in trade_rows]),4) if trade_rows else 0,
            "average_mae":round(mean([row["mae"] for row in trade_rows]),4) if trade_rows else 0,
            "stops":len(stop_trades),"targets":len(target_trades),"stopped_then_recovered_pct":{label:round(sum(row["stopped_then_recovered"][label] for row in stop_trades)/len(stop_trades)*100,2) if stop_trades else 0 for label in ("entry_1h","entry_4h","entry_1d")},
            "hypothetical_average_returns":{label:round(mean(values),4) if values else None for label in ("15m","30m","1h","2h","4h","1d") for values in [[row["hypothetical_hold"][label] for row in trade_rows if row["hypothetical_hold"][label] is not None]]}}
        return {"starting_equity":float(self.config.initial_balance),"ending_equity":float(summary["portfolio_value"]),
            "net_pnl":float(summary["total_pnl"]),"return_pct":float(summary["total_return_pct"]),"trades":len(trades),
            "wins":len(wins),"losses":len(losses),"win_rate":round(len(wins)/len(trades)*100,2) if trades else 0,
            "gross_profit":round(sum(wins),2),"gross_loss":round(sum(losses),2),
            "profit_factor":round(sum(wins)/abs(sum(losses)),4) if losses and sum(losses) else None,
            "average_win":round(mean(wins),2) if wins else 0,"average_loss":round(mean(losses),2) if losses else 0,
            "max_drawdown_pct":round(max_drawdown,4),"average_holding_hours":round(mean([row["holding_hours"] for row in trade_rows]),2) if trade_rows else 0,
            "median_holding_hours":round(median([row["holding_hours"] for row in trade_rows]),2) if trade_rows else 0,
            "fees":float(sum((item.commission for item in trades),Decimal(0))),"slippage_cost":float(sum((item.slippage_cost for item in trades),Decimal(0))),
            "exposure_pct":percent_from_fraction(exposure_samples),"peak_exposure_pct":round(max(exposure_samples,default=0)*100,4),
            "cash_utilization_pct":percent_from_fraction(exposure_samples),
            "average_actual_risk":round(mean(actual_risks),4) if actual_risks else 0,"risk_budget_utilization_pct":round(mean(risk_utilization),4) if risk_utilization else 0,
            "strategy_config_hash":config_snapshot["sha256"],"period":{"start":start.isoformat() if start else None,"end":end.isoformat() if end else None},
            "benchmark_return_pct":benchmark_return,"benchmark_max_drawdown_pct":benchmark_drawdown,
            "funnel":dict(funnel),"rejections":dict(rejections),"capital_diagnostics":capital_diagnostics,
            "allocation_timeline":allocation_timeline,"exit_diagnostics":exit_diagnostics,"trade_details":trade_rows}
