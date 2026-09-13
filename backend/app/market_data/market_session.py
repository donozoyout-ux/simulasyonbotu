from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


TIMEFRAME_DELTA = {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "1h": timedelta(hours=1), "1d": timedelta(days=1)}


@dataclass(frozen=True)
class BistMarketSession:
    timezone_name: str = "Europe/Istanbul"
    open_time: time = time(10, 0)
    close_time: time = time(18, 0)
    holidays: frozenset[date] = field(default_factory=frozenset)

    @classmethod
    def from_config(cls, config):
        def parse(value: str) -> time:
            hour, minute = map(int, value.split(":"))
            return time(hour, minute)
        return cls(config.bist_timezone, parse(config.bist_session_open), parse(config.bist_session_close))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def is_trading_day(self, value: date) -> bool:
        return value.weekday() < 5 and value not in self.holidays

    def is_open(self, at: datetime | None = None) -> bool:
        local = (at or datetime.now(timezone.utc)).astimezone(self.tz)
        return self.is_trading_day(local.date()) and self.open_time <= local.time() < self.close_time

    def previous_trading_day(self, value: date) -> date:
        candidate = value - timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate

    def candle_is_closed(self, candle_time: datetime, timeframe: str, now: datetime | None = None) -> bool:
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        candle_utc = candle_time.astimezone(timezone.utc)
        if timeframe == "1d":
            local_day = candle_utc.astimezone(self.tz).date()
            if local_day < now_utc.astimezone(self.tz).date():
                return True
            close_at = datetime.combine(local_day, self.close_time, self.tz).astimezone(timezone.utc)
            return now_utc >= close_at
        local_candle = candle_utc.astimezone(self.tz)
        scheduled = local_candle + TIMEFRAME_DELTA[timeframe]
        session_close = datetime.combine(local_candle.date(), self.close_time, self.tz)
        # Yahoo uses exchange-specific anchors (09:30 for 1h) and may emit an
        # 18:00 closing marker. Intraday bars can never close after the session.
        close_at = min(scheduled, session_close).astimezone(timezone.utc)
        return now_utc >= close_at

    def freshness(self, candle_time: datetime, timeframe: str, now: datetime | None = None, stale_minutes: int = 90) -> str:
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not self.candle_is_closed(candle_time, timeframe, now_utc):
            return "OPEN_CANDLE"
        local_now = now_utc.astimezone(self.tz)
        local_candle = candle_time.astimezone(self.tz)
        if self.is_open(now_utc):
            previous=self.previous_trading_day(local_now.date())
            if timeframe=="1d" and local_candle.date()==previous:
                return "FRESH"
            first_close=datetime.combine(local_now.date(),self.open_time,self.tz)+TIMEFRAME_DELTA[timeframe]
            if local_candle.date()==previous and local_now<first_close:
                return "FRESH"
            return "STALE" if now_utc-candle_time>timedelta(minutes=stale_minutes) else "FRESH"
        if self.is_trading_day(local_now.date()) and local_now.time()>=self.close_time:
            last_session=local_now.date()
        else:
            last_session=self.previous_trading_day(local_now.date())
        if local_candle.date()!=last_session:
            return "STALE"
        if timeframe=="1d":
            return "FRESH"
        candle_close=min(local_candle+TIMEFRAME_DELTA[timeframe],datetime.combine(last_session,self.close_time,self.tz))
        return "FRESH" if candle_close.time()>=self.close_time else "STALE"
