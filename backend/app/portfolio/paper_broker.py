from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Order, Portfolio, Position, Trade
from app.market_data.provider import CandleData

CENT=Decimal("0.01")


class DuplicateOrderError(ValueError): pass


def q(value:Decimal)->Decimal: return value.quantize(CENT,rounding=ROUND_HALF_UP)


class PaperBroker:
    def __init__(self,db:Session,commission_rate:Decimal,slippage_rate:Decimal,intrabar_policy:str="conservative",
                 run_id:str|None=None,strategy_version:str|None=None,strategy_config_hash:str|None=None):
        self.db,self.commission_rate,self.slippage_rate,self.intrabar_policy=db,commission_rate,slippage_rate,intrabar_policy
        self.run_id,self.strategy_version,self.strategy_config_hash=run_id,strategy_version,strategy_config_hash

    def buy(self,portfolio:Portfolio,symbol:str,quantity:int,price:Decimal,stop:Decimal,target:Decimal,setup:str,score:int,
            reason:str,idempotency_key:str|None=None,signal_candle_time:datetime|None=None,execution_time:datetime|None=None)->Position:
        if quantity<=0 or int(quantity)!=quantity: raise ValueError("Quantity pozitif integer olmalı")
        if not (stop<price<target): raise ValueError("Geçersiz entry/stop/target")
        if idempotency_key and self.db.scalar(select(Order.id).where(Order.idempotency_key==idempotency_key)):
            raise DuplicateOrderError(idempotency_key)
        fill=q(price*(Decimal(1)+self.slippage_rate));gross=q(fill*quantity)
        commission=q(gross*self.commission_rate);slippage_cost=q((fill-price)*quantity);total=gross+commission
        if portfolio.cash_balance<total: raise ValueError("Yetersiz nakit")
        portfolio.cash_balance-=total
        position=Position(symbol=symbol,quantity=quantity,entry_price=fill,current_price=fill,stop_price=stop,target_price=target,
            entry_fees=commission,entry_slippage_cost=slippage_cost,setup_type=setup,signal_score=score,entry_reason=reason,
            opened_at=execution_time or datetime.now().astimezone(),run_id=self.run_id,strategy_version=self.strategy_version)
        order=Order(symbol=symbol,side="BUY",quantity=quantity,requested_price=price,fill_price=fill,fees=commission,
            commission=commission,slippage_cost=slippage_cost,status="FILLED",reason=reason,idempotency_key=idempotency_key,
            signal_candle_time=signal_candle_time,created_at=execution_time or datetime.now().astimezone())
        order.run_id=self.run_id;order.strategy_version=self.strategy_version;order.strategy_config_hash=self.strategy_config_hash
        order.risk_amount=q((fill-stop)*quantity)
        self.db.add_all([position,order])
        try: self.db.commit()
        except IntegrityError as exc:
            self.db.rollback();raise DuplicateOrderError(idempotency_key or "duplicate") from exc
        self.db.refresh(position);return position

    def sell(self,portfolio:Portfolio,position:Position,quantity:int,price:Decimal,reason:str,side:str="SELL",execution_time:datetime|None=None)->Trade:
        if position.status!="OPEN" or quantity<=0 or quantity>position.quantity or int(quantity)!=quantity: raise ValueError("Geçersiz pozisyon/quantity")
        original_quantity=position.quantity
        fill=q(price*(Decimal(1)-self.slippage_rate));gross=q(fill*quantity)
        exit_commission=q(gross*self.commission_rate);exit_slippage=q((price-fill)*quantity)
        allocated_entry_fee=q(position.entry_fees*Decimal(quantity)/Decimal(original_quantity))
        allocated_entry_slippage=q(position.entry_slippage_cost*Decimal(quantity)/Decimal(original_quantity))
        pnl=q((fill-position.entry_price)*quantity-exit_commission-allocated_entry_fee)
        portfolio.cash_balance+=gross-exit_commission;portfolio.realized_pnl+=pnl
        trade=Trade(position_id=position.id,symbol=position.symbol,quantity=quantity,entry_price=position.entry_price,exit_price=fill,
            entry_time=position.opened_at,position_value=q(position.entry_price*quantity),stop_price=position.stop_price,
            target_price=position.target_price,fees=exit_commission+allocated_entry_fee,commission=exit_commission+allocated_entry_fee,
            slippage_cost=exit_slippage+allocated_entry_slippage,realized_pnl=pnl,
            return_pct=pnl/(position.entry_price*quantity)*100,setup_type=position.setup_type,signal_score=position.signal_score,
            entry_reason=position.entry_reason,exit_reason=reason,exit_time=execution_time or datetime.now().astimezone())
        trade.run_id=position.run_id or self.run_id;trade.strategy_version=position.strategy_version or self.strategy_version
        position.quantity-=quantity;position.entry_fees-=allocated_entry_fee;position.entry_slippage_cost-=allocated_entry_slippage
        if position.quantity==0: position.status="CLOSED"
        order=Order(symbol=position.symbol,side=side,quantity=quantity,requested_price=price,fill_price=fill,fees=exit_commission,
            commission=exit_commission,slippage_cost=exit_slippage,status="FILLED",reason=reason,
            created_at=execution_time or datetime.now().astimezone(),run_id=position.run_id or self.run_id,
            strategy_version=position.strategy_version or self.strategy_version,strategy_config_hash=self.strategy_config_hash)
        self.db.add_all([trade,order]);self.db.commit();self.db.refresh(trade);return trade

    def evaluate_candle(self,portfolio:Portfolio,position:Position,candle:CandleData,execution_time:datetime|None=None):
        position.current_price=candle.close
        stop_hit=candle.low<=position.stop_price;target_hit=candle.high>=position.target_price
        if stop_hit and target_hit:
            if self.intrabar_policy!="conservative": raise ValueError("Desteklenmeyen intrabar policy")
            return self.sell(portfolio,position,position.quantity,position.stop_price,"Aynı mum stop+hedef; conservative STOP önce","STOP_LOSS",execution_time)
        if stop_hit:return self.sell(portfolio,position,position.quantity,position.stop_price,"Fixed stop loss tetiklendi","STOP_LOSS",execution_time)
        if target_hit:return self.sell(portfolio,position,position.quantity,position.target_price,"Fixed take profit tetiklendi","TAKE_PROFIT",execution_time)
        self.db.commit();return None

    def evaluate_position(self,portfolio:Portfolio,position:Position,latest_price:Decimal):
        candle=CandleData(position.opened_at,latest_price,latest_price,latest_price,latest_price,Decimal(0),True)
        return self.evaluate_candle(portfolio,position,candle)
