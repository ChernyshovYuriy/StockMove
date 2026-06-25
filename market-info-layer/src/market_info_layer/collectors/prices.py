from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from market_info_layer.db.models import Price, Watchlist
from market_info_layer.utils.time import utc_now_iso

US_MARKET_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    price_date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    source: str


class PriceProvider(ABC):
    @abstractmethod
    def daily_prices(
        self,
        ticker: str,
        *,
        period: str = "2y",
        start: str | None = None,
        end: str | None = None,
    ) -> list[PriceBar]:
        raise NotImplementedError


class YFinancePriceProvider(PriceProvider):
    source = "yfinance"

    def daily_prices(
        self,
        ticker: str,
        *,
        period: str = "2y",
        start: str | None = None,
        end: str | None = None,
    ) -> list[PriceBar]:
        import yfinance as yf

        kwargs = {"interval": "1d", "auto_adjust": False, "progress": False}
        if start or end:
            kwargs.update({"start": start, "end": end})
        else:
            kwargs["period"] = period
        frame = yf.download(ticker, **kwargs)
        if frame.empty:
            return []
        if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
            frame.columns = frame.columns.get_level_values(0)
        bars: list[PriceBar] = []
        for idx, row in frame.iterrows():
            price_date = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
            bars.append(
                PriceBar(
                    ticker=ticker.upper(),
                    price_date=price_date,
                    open=_float_or_none(row.get("Open")),
                    high=_float_or_none(row.get("High")),
                    low=_float_or_none(row.get("Low")),
                    close=_float_or_none(row.get("Close")),
                    volume=_int_or_none(row.get("Volume")),
                    source=self.source,
                )
            )
        return bars


class PlaceholderPriceProvider(PriceProvider):
    def daily_prices(
        self,
        ticker: str,
        *,
        period: str = "2y",
        start: str | None = None,
        end: str | None = None,
    ) -> list[PriceBar]:
        return []


def _float_or_none(value) -> float | None:
    return None if value is None else float(value)


def _int_or_none(value) -> int | None:
    return None if value is None else int(value)


def _watchlist_tickers(session: Session) -> list[str]:
    return [
        row.ticker
        for row in session.query(Watchlist).filter(Watchlist.status != "inactive").all()
    ]


def _us_trading_today() -> date:
    return datetime.now(US_MARKET_TIMEZONE).date()


def _bar_complete(price_date: str, today: date) -> bool:
    return date.fromisoformat(price_date) < today


def collect_prices(
    session: Session,
    *,
    provider: PriceProvider | None = None,
    ticker: str | None = None,
    period: str = "2y",
    start: str | None = None,
    end: str | None = None,
    include_current_day: bool = False,
) -> int:
    provider = provider or YFinancePriceProvider()
    tickers = [ticker.upper()] if ticker else _watchlist_tickers(session)
    inserted = 0
    collected_at = utc_now_iso()
    today = _us_trading_today()
    for symbol in tickers:
        for bar in provider.daily_prices(symbol, period=period, start=start, end=end):
            bar_date = date.fromisoformat(bar.price_date)
            if bar_date >= today and not include_current_day:
                continue
            is_complete = _bar_complete(bar.price_date, today)
            stmt = sqlite_insert(Price).values(
                ticker=bar.ticker.upper(),
                price_date=bar.price_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                is_complete=is_complete,
                source=bar.source,
                collected_at=collected_at,
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=["ticker", "price_date", "source"])
            result = session.execute(stmt)
            inserted += result.rowcount or 0
    session.commit()
    return inserted
