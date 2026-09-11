from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.market_data.market_session import BistMarketSession
from app.models import ProcessedCandle, ScanRun
from app.scanner.bist_scanner import BistScanner
from app.services.forward_test import ensure_forward_run


def expected_closed_candle(config,now:datetime|None=None):
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    session=BistMarketSession.from_config(config);local=now.astimezone(session.tz)
    if not session.is_trading_day(local.date()) or local.time()<session.open_time or local.time()>session.close_time:return None
    opened=datetime.combine(local.date(),session.open_time,session.tz)
    elapsed=(local-opened).total_seconds()
    completed=int(elapsed//900)
    if completed<1:return None
    # The idempotency key is the candle's close, not its opening timestamp.
    return (opened+timedelta(minutes=completed*15)).astimezone(timezone.utc)


class ForwardWorker:
    def __init__(self,db,config,provider=None):self.db,self.config,self.provider=db,config,provider

    def run_once(self,now:datetime|None=None,max_symbols:int|None=None):
        if self.config.operation_mode!="LIVE_PAPER":return {"status":"wrong_mode"}
        stamp=expected_closed_candle(self.config,now)
        if stamp is None:return {"status":"market_closed"}
        run=ensure_forward_run(self.db,self.config,now)
        marker=self.db.scalar(select(ProcessedCandle).where(ProcessedCandle.run_id==run.run_id,
            ProcessedCandle.timeframe=="15m",ProcessedCandle.closed_candle_timestamp==stamp))
        if marker and marker.status=="COMPLETE":return {"status":"already_processed","closed_candle_timestamp":stamp.isoformat()}
        if not marker:
            marker=ProcessedCandle(run_id=run.run_id,timeframe="15m",closed_candle_timestamp=stamp,status="STARTED")
            self.db.add(marker)
            try:self.db.commit();self.db.refresh(marker)
            except IntegrityError:
                self.db.rollback();return {"status":"already_processing","closed_candle_timestamp":stamp.isoformat()}
        try:
            scanner=BistScanner(self.db,self.config,self.provider)
            result=scanner.run(max_symbols,stamp)
            try:
                benchmark=scanner.provider.get_latest_price("XU100")
                if run.benchmark_start_price is None:run.benchmark_start_price=benchmark
                run.benchmark_latest_price=benchmark;run.benchmark_updated_at=datetime.now(timezone.utc);self.db.commit()
            except Exception:
                self.db.rollback()
            scan=self.db.scalar(select(ScanRun).where(ScanRun.run_id==run.run_id,
                ScanRun.closed_candle_timestamp==stamp).order_by(ScanRun.id.desc()).limit(1))
            marker.status="COMPLETE";marker.error=None;marker.scan_run_id=scan.id if scan else None;self.db.commit()
            return {**result,"closed_candle_timestamp":stamp.isoformat()}
        except Exception as exc:
            self.db.rollback();marker=self.db.get(ProcessedCandle,marker.id)
            marker.status="FAILED";marker.error=str(exc);self.db.commit();raise
