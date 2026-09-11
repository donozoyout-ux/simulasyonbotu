from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config.settings import AppSettings
from app.db.session import Base
from app.market_data.provider import CandleData
from app.models import Candle
from app.replay.portfolio_engine import PortfolioReplayEngine
from app.research.dataset import load_dataset, save_dataset
from app.research.diagnostics import COMPONENT_MAX, is_near_miss, sequential_counts, stats
from app.strategy.scoring_engine import calculate_score
from app.strategy.setup_engine import structural_target
from app.strategy.setup_engine import detect_setup
from app.scanner.bist_scanner import BistScanner


def candle(at,price=Decimal("100")):
    return CandleData(at,price,price+1,price-1,price,Decimal("1000"),True)


def test_score_component_utilization_and_sequential_funnel():
    score,breakdown=calculate_score({key:1 for key in COMPONENT_MAX})
    assert score==100 and breakdown==COMPONENT_MAX
    assert stats([2,5,10])["max"]==10
    funnel=sequential_counts([{"complete":True,"setup":True,"score":False},{"complete":True,"setup":False,"score":True}],
                             ("complete","setup","score"))
    assert funnel=={"complete":2,"setup":1,"score":0}


def test_near_miss_and_structural_target_are_deterministic():
    diagnostic={"candidate":True,"detected":False,"failed_rules":["volume_ok"],"quality_score":71}
    assert is_near_miss(diagnostic)
    levels={"zones":[{"side":"RESISTANCE","low":Decimal("108")},{"side":"RESISTANCE","low":Decimal("112")}]}
    assert structural_target(Decimal("105"),levels,Decimal("2"))==(Decimal("108"),"next_resistance_zone")
    assert structural_target(Decimal("120"),levels,Decimal("2"))==(Decimal("126"),"atr_projection_no_resistance")


def test_canonical_dataset_bytes_are_reproducible(tmp_path:Path):
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    frames={"ASELS":{tf:[candle(start+timedelta(minutes=15*i)) for i in range(3)] for tf in ("1d","1h","15m")}}
    first=tmp_path/"a.json.gz";second=tmp_path/"b.json.gz"
    meta_a=save_dataset(first,frames,"test");meta_b=save_dataset(second,frames,"test")
    assert meta_a["sha256"]==meta_b["sha256"]
    assert first.read_bytes()==second.read_bytes()
    assert load_dataset(first)[0]["row_count"]==9


def test_canonical_dataset_accepts_explicit_evaluation_boundaries(tmp_path:Path):
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    frames={"ASELS":{tf:[candle(start+timedelta(minutes=15*i)) for i in range(3)] for tf in ("1d","1h","15m")}}
    target=tmp_path/"bounded.json.gz"
    save_dataset(target,frames,"test",extra_metadata={"evaluation_start":start.isoformat(),"evaluation_15m_rows":3})
    metadata,_,_=load_dataset(target)
    assert metadata["evaluation_start"]==start.isoformat()
    assert metadata["evaluation_15m_rows"]==3


def test_portfolio_replay_ranks_candidates_and_shares_cash():
    end=datetime(2026,9,10,12,tzinfo=timezone.utc)
    frames={symbol:{
        "15m":[candle(end-timedelta(minutes=15*(104-i)),Decimal("100")) for i in range(105)],
        "1h":[candle(end-timedelta(hours=104-i),Decimal("100")) for i in range(105)],
        "1d":[candle(end-timedelta(days=104-i),Decimal("100")) for i in range(105)],
    } for symbol in ("LOW","HIGH")}
    def result(symbol,*_args,**_kwargs):
        score=90 if symbol=="HIGH" else 85
        return SimpleNamespace(symbol=symbol,price=Decimal("100"),score=score,setup="BREAKOUT",trend="bullish",
            signal_candle_time=end,decision="POSSIBLE_ENTRY",reason="test",funnel={"valid_setup":True,"rr_pass":True},
            details={"analysis_complete":True,"risk_reward":Decimal("2"),"volatility":{"atr":Decimal("2")},
                     "setup":{"score":80,"invalidation_level":Decimal("95"),"target":Decimal("110")}})
    config=AppSettings(initial_balance=Decimal("5000"),max_open_positions=1,entry_score=82,strategy_version="v3")
    outputs=[]
    for _ in range(2):
        engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine)
        with Session(engine,expire_on_commit=False) as db,patch("app.replay.portfolio_engine.analyze_frames",side_effect=result):
            outputs.append(PortfolioReplayEngine(db,config).run(frames,analysis_step=1))
    assert outputs[0]==outputs[1]
    assert outputs[0]["trades"]==1
    assert outputs[0]["trade_details"][0]["symbol"]=="HIGH"
    assert outputs[0]["rejections"]["risk_rejected"]>=1


def test_portfolio_replay_uses_explicit_evaluation_window():
    end=datetime(2026,1,10,12,tzinfo=timezone.utc)
    trigger=[candle(end-timedelta(minutes=15*(104-i)),Decimal("100")) for i in range(105)]
    frames={"TEST":{"15m":trigger,"1h":[candle(end-timedelta(hours=104-i)) for i in range(105)],
                    "1d":[candle(end-timedelta(days=104-i)) for i in range(105)]}}
    evaluation_start=trigger[-3].timestamp;evaluation_end=trigger[-2].timestamp
    engine=create_engine("sqlite:///:memory:");Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        result=PortfolioReplayEngine(db,AppSettings(strategy_version="v3")).run(
            frames,analysis_step=99,evaluation_start=evaluation_start,evaluation_end=evaluation_end)
    assert result["period"]=={"start":evaluation_start.isoformat(),"end":evaluation_end.isoformat()}


def test_small_capital_rejection_is_reported_by_risk_engine():
    from app.portfolio.risk_manager import size_position
    result=size_position(Decimal("5000"),Decimal("5000"),Decimal("6000"),Decimal("5900"),Decimal("6200"),
        Decimal(".005"),Decimal(".2"),Decimal(".1"),0,4,Decimal("1.5"),Decimal("100"))
    assert not result.approved and "en az 1 lot" in result.reason


def test_pullback_stop_and_target_use_structure():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    candles=[CandleData(start+timedelta(minutes=15*i),Decimal("100"),Decimal("103"),Decimal("97"),Decimal("100"),Decimal("1000"),True) for i in range(29)]
    candles.append(CandleData(start+timedelta(minutes=15*29),Decimal("98.5"),Decimal("100"),Decimal("98.2"),Decimal("99.8"),Decimal("1200"),True))
    levels={"support":Decimal("98.5"),"support_zone":{"low":Decimal("98"),"high":Decimal("99"),"strength":80},
            "resistance_zone":{"low":Decimal("105"),"high":Decimal("106")},
            "zones":[{"side":"SUPPORT","low":Decimal("98")},{"side":"RESISTANCE","low":Decimal("105")}],"breakout_zone":None}
    result=detect_setup(candles,{"complete":True,"rvol":Decimal("1.2"),"volume_expansion":True,"confirmed":False},
        {"complete":True,"label":"bullish"},{"complete":True,"label":"BULLISH"},levels,Decimal("1"),
        {"bullish":True,"close_location":Decimal("0.89"),"quality_score":80,"body_pct":Decimal("0.72"),"upper_wick_pct":Decimal("0.1")},False)
    assert result.detected and result.setup_type=="PULLBACK"
    assert result.invalidation_level<Decimal("98")
    assert result.target==Decimal("105")


def test_scanner_candle_persistence_is_idempotent_after_sqlite_timezone_roundtrip():
    engine=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    at=datetime(2026,9,10,9,tzinfo=timezone.utc)
    frames={"15m":[candle(at),candle(at+timedelta(minutes=15))]}
    with Session(engine,expire_on_commit=False) as db:
        scanner=BistScanner(db,AppSettings(data_mode="mock"),provider=SimpleNamespace(name="test"))
        scanner._persist_candles("ASELS",frames,"test");db.commit()
        scanner._persist_candles("ASELS",frames,"test");db.commit()
        assert len(db.scalars(select(Candle)).all())==2
        assert db.scalar(select(Candle.symbol).limit(1))=="ASELS"
