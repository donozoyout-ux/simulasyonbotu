from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic
import httpx
from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.market_data.market_session import BistMarketSession
from app.models import BackfillState, NewsCompanyLink, NewsItem, NewsMarketReaction, NewsSourceState
from app.news.company_aliases import CompanyResolution, resolve_company_symbols
from app.news.dedupe import content_hash
from app.news.sentiment import GroqNewsAnalyzer
from app.news.sources.registry import SOURCE_REGISTRY, build_sources, source_spec
from app.news.telegram import TelegramNewsNotifier
from app.services.collection_activity import log_activity

_REFRESH_LOCK=Lock(); _LAST_REFRESH_AT=0.0; _LAST_REFRESH_BIND=None


def _utc(value):
    if value is None: return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class NewsService:
    def __init__(self,db,config,sources=None,analyzer=None,notifier=None):
        self.db,self.config=db,config
        self.sources=sources if sources is not None else build_sources(config)
        self.analyzer=analyzer or GroqNewsAnalyzer(config)
        self.notifier=notifier or TelegramNewsNotifier(config)

    def list(self,symbol=None,source=None,sentiment=None,min_importance=None,limit=100):
        stmt=select(NewsItem)
        if symbol: stmt=stmt.where(NewsItem.symbol==symbol.upper())
        if source: stmt=stmt.where(NewsItem.source==source.upper())
        if sentiment: stmt=stmt.where(NewsItem.ai_sentiment==sentiment.upper())
        if min_importance is not None: stmt=stmt.where(NewsItem.ai_importance>=min_importance)
        return self.db.scalars(stmt.order_by(desc(NewsItem.published_at)).limit(limit)).all()

    def archive(self,symbol=None,source=None,category=None,sentiment=None,min_importance=None,
                start=None,end=None,overnight_only=False,reaction_only=False,limit=500,reaction_complete_only=False):
        stmt=select(NewsItem,NewsMarketReaction).outerjoin(NewsMarketReaction,
            (NewsMarketReaction.news_id==NewsItem.id)&(NewsMarketReaction.symbol==NewsItem.symbol))
        if symbol: stmt=stmt.where(NewsItem.symbol==symbol.upper())
        if source: stmt=stmt.where(NewsItem.source==source.upper())
        if category: stmt=stmt.where(NewsItem.category==category.upper())
        if sentiment: stmt=stmt.where(NewsItem.ai_sentiment==sentiment.upper())
        if min_importance is not None: stmt=stmt.where(NewsItem.ai_importance>=min_importance)
        if start: stmt=stmt.where(NewsItem.published_at>=start)
        if end: stmt=stmt.where(NewsItem.published_at<=end)
        if overnight_only: stmt=stmt.where(NewsItem.overnight_news.is_(True))
        if reaction_only: stmt=stmt.where(NewsMarketReaction.id.is_not(None))
        if reaction_complete_only: stmt=stmt.where(NewsMarketReaction.status=="COMPLETE")
        rows=self.db.execute(stmt.order_by(desc(NewsItem.published_at)).limit(limit)).all()
        item_ids=[item.id for item,_ in rows]
        links_by_news={item_id:[] for item_id in item_ids}
        if item_ids:
            for link in self.db.scalars(select(NewsCompanyLink).where(NewsCompanyLink.news_id.in_(item_ids))
                    .order_by(NewsCompanyLink.news_id,desc(NewsCompanyLink.is_primary),desc(NewsCompanyLink.confidence))).all():
                links_by_news[link.news_id].append({c.name:getattr(link,c.name) for c in NewsCompanyLink.__table__.columns})
        result=[]
        for item,reaction in rows:
            payload={c.name:getattr(item,c.name) for c in NewsItem.__table__.columns}
            payload["reaction"]={c.name:getattr(reaction,c.name) for c in NewsMarketReaction.__table__.columns} if reaction else None
            payload["company_links"]=links_by_news[item.id]
            result.append(payload)
        return result

    @staticmethod
    def _resolve(record):
        return resolve_company_symbols(record.title,record.content,record.company_name or "",
            structured_symbol=record.symbol,structured_company=record.company_name)

    def _apply_resolution(self,item:NewsItem,resolution:CompanyResolution,now:datetime,*,replace_links:bool=True)->int:
        prior_symbol,prior_company=item.symbol,item.company_name
        primary=next((x for x in resolution.links if x.symbol==resolution.primary_symbol),None)
        item.symbol=resolution.primary_symbol
        item.company_name=primary.detected_company if primary else prior_company
        item.symbol_match_method=primary.match_method if primary else "UNMATCHED"
        item.symbol_match_confidence=primary.confidence if primary else None
        item.unmatched_reason=resolution.unmatched_reason
        item.detected_company=primary.detected_company if primary else None
        item.best_candidate=resolution.best_candidate
        item.best_candidate_confidence=resolution.best_confidence
        self.db.flush()
        if replace_links:
            self.db.execute(delete(NewsCompanyLink).where(NewsCompanyLink.news_id==item.id))
        for link in resolution.links:
            self.db.add(NewsCompanyLink(news_id=item.id,symbol=link.symbol,confidence=link.confidence,
                match_method=link.match_method,is_primary=link.symbol==resolution.primary_symbol))
        reactions_created=0
        reactions=self.db.scalars(select(NewsMarketReaction).where(NewsMarketReaction.news_id==item.id)).all()
        valid_symbols={link.symbol for link in resolution.links}
        for reaction in reactions:
            if reaction.symbol not in valid_symbols and reaction.status!="ERROR":
                reaction.status="ERROR";reaction.error="SYMBOL_MAPPING_REVOKED";reaction.next_evaluation_at=None
        existing_symbols={reaction.symbol for reaction in reactions}
        for link in resolution.links:
            if link.symbol not in existing_symbols:
                self.db.add(NewsMarketReaction(news_id=item.id,symbol=link.symbol,status="PENDING",next_evaluation_at=now))
                reactions_created+=1
        if prior_symbol!=item.symbol:
            log_activity(self.db,"NEWS",item.source,"REMAP","OK",f"{prior_symbol or 'UNMATCHED'} -> {item.symbol or 'UNMATCHED'}")
        return reactions_created

    @staticmethod
    def _apply_spec(state,spec):
        if not spec:return
        state.source_type,state.base_url,state.enabled=spec.type,spec.base_url,spec.enabled
        state.priority,state.poll_interval=spec.priority,spec.poll_interval
        state.supports_backfill,state.supports_symbol_mapping=spec.supports_backfill,spec.supports_symbol_mapping

    def _news_backfill_state(self,now):
        state=self.db.get(BackfillState,"NEWS_ARCHIVE") or BackfillState(task="NEWS_ARCHIVE")
        supported=[s.id for s in SOURCE_REGISTRY if s.enabled and s.supports_backfill]
        state.status="PARTIAL" if supported else "SOURCE_LIMITED"; state.last_run_at=now
        state.processed_items=self.db.scalar(select(func.count()).select_from(NewsItem)) or 0
        state.details={"requested_days":self.config.news_backfill_days,"mode":"incremental_feed_archive",
            "historical_source":state.status,"supporting_sources":supported,"telegram_eligible":False}
        self.db.add(state)

    def refresh(self,backfill=False):
        global _LAST_REFRESH_AT,_LAST_REFRESH_BIND
        if not self.config.news_enabled:return {"status":"DISABLED","new_items":0,"errors":[]}
        with _REFRESH_LOCK:
            current,bind=monotonic(),self.db.get_bind()
            if bind is _LAST_REFRESH_BIND and current-_LAST_REFRESH_AT<60:
                return {"status":"RATE_LIMITED","new_items":0,"errors":[]}
            _LAST_REFRESH_AT,_LAST_REFRESH_BIND=current,bind
        total,errors,skipped=0,[],[]; now=datetime.now(timezone.utc)
        session=BistMarketSession.from_config(self.config)
        for source in self.sources:
            spec=getattr(source,"spec",None) or source_spec(source.name)
            state=self.db.get(NewsSourceState,source.name) or NewsSourceState(source=source.name)
            self._apply_spec(state,spec); self.db.add(state)
            if state.enabled is False: state.status="DISABLED"; skipped.append(source.name); continue
            if _utc(state.next_poll_at) and _utc(state.next_poll_at)>now: skipped.append(source.name); continue
            interval=max(spec.poll_interval if spec else 300,
                (self.config.news_poll_minutes_open if session.is_open(now) else self.config.news_poll_minutes_closed)*60)
            state.last_poll_at=state.last_fetch_at=now; state.next_poll_at=now+timedelta(seconds=interval)
            try:
                records=source.fetch(); added=duplicates=0
                state.items_fetched=(state.items_fetched or 0)+len(records)
                for record in records:
                    resolution=self._resolve(record)
                    primary=next((x for x in resolution.links if x.symbol==resolution.primary_symbol),None)
                    symbol,company_name=resolution.primary_symbol,primary.detected_company if primary else record.company_name
                    digest=content_hash(record.source,record.title,record.content,record.url)
                    exists=self.db.scalar(select(NewsItem).where(or_(NewsItem.content_hash==digest,
                        (NewsItem.source==record.source)&(NewsItem.source_id==record.source_id))))
                    if exists:
                        exists.updated_at=now
                        self._apply_resolution(exists,resolution,now)
                        duplicates+=1;continue
                    payload={**record.__dict__,"symbol":symbol,"company_name":company_name,
                        "source_metadata":record.source_metadata or {}}
                    item=NewsItem(**payload,content_hash=digest,first_seen_at=now,fetched_at=now,updated_at=now,
                        overnight_news=not session.is_open(record.published_at),telegram_eligible=not backfill,
                        ingestion_mode="BACKFILL" if backfill else "LIVE",
                        symbol_match_method=primary.match_method if primary else "UNMATCHED",
                        symbol_match_confidence=primary.confidence if primary else None,
                        unmatched_reason=resolution.unmatched_reason,detected_company=primary.detected_company if primary else record.company_name,
                        best_candidate=resolution.best_candidate,best_candidate_confidence=resolution.best_confidence)
                    if item.symbol:
                        result=self.analyzer.evaluate(payload); item.ai_status=result["status"]; item.ai_model=result.get("model")
                        if result["status"]=="OK":
                            item.ai_sentiment,item.ai_importance=result["sentiment"],result["importance"]
                            item.ai_summary,item.ai_horizon=result["summary"],result["horizon"]
                            item.ai_risks,item.ai_tags=result["risks"],result["tags"]
                    else:item.ai_status="SKIPPED_UNMATCHED"
                    try:
                        with self.db.begin_nested():self.db.add(item);self.db.flush()
                    except IntegrityError:duplicates+=1;continue
                    for link in resolution.links:
                        self.db.add(NewsCompanyLink(news_id=item.id,symbol=link.symbol,confidence=link.confidence,
                            match_method=link.match_method,is_primary=link.symbol==item.symbol))
                        self.db.add(NewsMarketReaction(news_id=item.id,symbol=link.symbol,
                            status="WAITING_MARKET_OPEN" if item.overnight_news else "WAITING_15M",next_evaluation_at=now))
                    if item.telegram_eligible and self.notifier.send(item):item.telegram_sent=True;item.telegram_sent_at=now
                    added+=1
                state.status="OK" if records else "EMPTY";state.last_success_at=now
                state.last_item_at=max((_utc(x.published_at) for x in records),default=state.last_item_at)
                state.last_seen_id=records[0].source_id if records else state.last_seen_id
                state.new_items=added;state.items_inserted=(state.items_inserted or 0)+added
                state.duplicates=(state.duplicates or 0)+duplicates;state.error=None;state.consecutive_failures=0
                total+=added;log_activity(self.db,"NEWS",source.name,"FETCH",state.status,
                    f"{len(records)} fetched, {added} new, {duplicates} duplicate")
                self._news_backfill_state(now);self.db.commit()
            except Exception as exc:
                self.db.rollback();state=self.db.get(NewsSourceState,source.name) or NewsSourceState(source=source.name)
                self._apply_spec(state,spec)
                waf=isinstance(exc,httpx.HTTPStatusError) and 400<=exc.response.status_code<700
                state.status="WAF_BLOCKED" if source.name=="KAP" and waf else "ERROR"
                state.last_poll_at=state.last_fetch_at=now;state.next_poll_at=now+timedelta(seconds=interval)
                state.error=f"{type(exc).__name__}: source unavailable"[:500]
                state.errors=(state.errors or 0)+1;state.consecutive_failures=(state.consecutive_failures or 0)+1
                if isinstance(exc,(ValueError,TypeError)):state.parse_errors=(state.parse_errors or 0)+1
                self.db.add(state);log_activity(self.db,"NEWS",source.name,"FETCH",state.status,state.error)
                self.db.commit();errors.append({"source":source.name,"error":state.error})
        return {"status":"OK" if not errors else "PARTIAL","new_items":total,"errors":errors,"skipped":skipped}

    def reconcile_symbols(self,batch_size:int=50):
        limit=max(1,min(int(batch_size),100));now=datetime.now(timezone.utc)
        before=self.metrics();state=self.db.get(BackfillState,"NEWS_SYMBOL_RECONCILE") or BackfillState(task="NEWS_SYMBOL_RECONCILE")
        rows=self.db.scalars(select(NewsItem).where(NewsItem.symbol_match_method.is_(None))
            .order_by(NewsItem.id).limit(limit)).all()
        linked=unlinked=reactions_created=0
        for item in rows:
            resolution=resolve_company_symbols(item.title,item.content,item.company_name or "")
            reactions_created+=self._apply_resolution(item,resolution,now)
            if item.symbol:linked+=1
            else:unlinked+=1
        self.db.flush()
        pending=self.db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.symbol_match_method.is_(None))) or 0
        state.status="COMPLETE" if pending==0 else "RUNNING";state.cursor=rows[-1].id if rows else state.cursor
        state.processed_items=(state.processed_items or 0)+len(rows);state.last_run_at=now
        state.details={"last_cycle_processed":len(rows),"last_cycle_linked":linked,"last_cycle_unmatched":unlinked,
            "last_cycle_reactions_created":reactions_created,"pending":pending}
        self.db.add(state);log_activity(self.db,"NEWS","RECONCILE","BATCH",state.status,
            f"{len(rows)} processed, {linked} linked, {pending} pending")
        self.db.commit();after=self.metrics()
        return {"status":state.status,"processed":len(rows),"linked":linked,"unmatched":unlinked,
            "reactions_created":reactions_created,"pending":pending,"before":before,"after":after}

    def unmatched(self,limit:int=100):
        rows=self.db.scalars(select(NewsItem).where(NewsItem.symbol.is_(None)).order_by(desc(NewsItem.published_at)).limit(limit)).all()
        fields=("id","source","title","published_at","unmatched_reason","best_candidate","best_candidate_confidence")
        return [{field:getattr(item,field) for field in fields} for item in rows]

    def sources_health(self):
        states={r.source:r for r in self.db.scalars(select(NewsSourceState)).all()}; result=[]
        for spec in SOURCE_REGISTRY:
            row=states.get(spec.id); enabled=spec.enabled and not(spec.id=="KAP" and not self.config.kap_enabled)
            result.append({"source":spec.id,"name":spec.name,"type":spec.type,"base_url":spec.base_url,
                "enabled":enabled,"priority":spec.priority,"poll_interval":spec.poll_interval,
                "supports_backfill":spec.supports_backfill,"supports_symbol_mapping":spec.supports_symbol_mapping,
                "status":row.status if row else "DISABLED" if not enabled else "NO_DATA",
                "last_attempt_at":(row.last_poll_at or row.last_fetch_at) if row else None,
                "last_success_at":row.last_success_at if row else None,"last_item_at":row.last_item_at if row else None,
                "next_poll_at":row.next_poll_at if row else None,"items_fetched":row.items_fetched if row else 0,
                "items_inserted":row.items_inserted if row else 0,"duplicates":row.duplicates if row else 0,
                "errors":row.errors if row else 0,"last_error":row.error if row else None,
                "consecutive_failures":row.consecutive_failures if row else 0})
        return result

    def metrics(self):
        now=datetime.now(timezone.utc)
        def count(*where):return self.db.scalar(select(func.count()).select_from(NewsItem).where(*where)) or 0
        reactions=dict(self.db.execute(select(NewsMarketReaction.status,func.count()).group_by(NewsMarketReaction.status)).all())
        waits=("WAITING_MARKET_OPEN","WAITING_15M","WAITING_1H","WAITING_1D","WAITING_5D")
        total=count();linked=count(NewsItem.symbol.is_not(None));pending=count(NewsItem.symbol_match_method.is_(None))
        methods=dict(self.db.execute(select(NewsItem.symbol_match_method,func.count()).where(
            NewsItem.symbol.is_not(None)).group_by(NewsItem.symbol_match_method)).all())
        reasons=dict(self.db.execute(select(NewsItem.unmatched_reason,func.count()).where(
            NewsItem.symbol.is_(None),NewsItem.unmatched_reason.is_not(None)).group_by(NewsItem.unmatched_reason)).all())
        state=self.db.get(BackfillState,"NEWS_SYMBOL_RECONCILE")
        return {"total_news":total,"news_last_24h":count(NewsItem.published_at>=now-timedelta(days=1)),
            "news_last_7d":count(NewsItem.published_at>=now-timedelta(days=7)),
            "news_last_30d":count(NewsItem.published_at>=now-timedelta(days=30)),
            "symbol_linked":linked,"unmatched":count(NewsItem.symbol.is_(None)),"link_rate_pct":round(linked*100/total,2) if total else 0,
            "reconcile_pending":pending,"reconciled_last_cycle":(state.details or {}) if state else {},
            "match_methods":{str(k):v for k,v in methods.items() if k},"unmatched_reasons":{str(k):v for k,v in reasons.items() if k},
            "unmatched_news":count(NewsItem.symbol.is_(None)),"ai_processed":count(NewsItem.ai_status=="OK"),
            "ai_pending":count(or_(NewsItem.ai_status.is_(None),NewsItem.ai_status.in_(["PENDING","AI_UNAVAILABLE","RATE_LIMITED"]))),
            "overnight_count":count(NewsItem.overnight_news.is_(True)),"reactions_pending":reactions.get("PENDING",0),
            "reactions_partial":sum(reactions.get(x,0) for x in waits),"reactions_complete":reactions.get("COMPLETE",0)}

    def health(self):
        sources,metrics=self.sources_health(),self.metrics();failures={"ERROR","WAF_BLOCKED","RATE_LIMITED"}
        enabled=[x for x in sources if x["enabled"]]
        status="NO_DATA" if not enabled else "ERROR" if all(x["status"] in failures for x in enabled) else "PARTIAL" if any(x["status"] in failures for x in enabled) else "OK"
        return {"enabled":self.config.news_enabled,"configured":True,"status":status,
            "sources":{x["source"]:{"status":x["status"],"last_fetch":x["last_attempt_at"],"new_items":x["items_inserted"],"parse_errors":0,"error":x["last_error"]} for x in sources},
            "groq_processed":metrics["ai_processed"],"pending_ai":metrics["ai_pending"],"last_24h":metrics["news_last_24h"],
            "important_24h":self.db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.published_at>=datetime.now(timezone.utc)-timedelta(days=1),NewsItem.ai_importance>=self.config.news_telegram_min_importance)) or 0,
            "overnight_24h":self.db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.published_at>=datetime.now(timezone.utc)-timedelta(days=1),NewsItem.overnight_news.is_(True))) or 0,
            "kap_24h":self.db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.published_at>=datetime.now(timezone.utc)-timedelta(days=1),NewsItem.source=="KAP")) or 0,
            "errors":sum(x["status"] in failures for x in enabled),**metrics}
