from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.db.models import Price


@dataclass(frozen=True)
class EventPriceReaction:
    close_prev: float | None
    close_event_or_next: float | None
    close_plus_1: float | None
    close_plus_5: float | None
    pct_1d: float | None
    pct_5d: float | None
    volume_event: int | None
    avg_volume_20d: float | None
    volume_ratio: float | None


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return date.fromisoformat(value[:10])


def _pct(start: float | None, end: float | None) -> float | None:
    if start in (None, 0) or end is None:
        return None
    return ((end - start) / start) * 100


def event_price_reaction(
    session: Session, ticker: str, event_date: str | date
) -> EventPriceReaction:
    """Return conservative price/volume context around an event date."""

    parsed_date = _parse_date(event_date)
    rows = session.scalars(
        select(Price)
        .where(Price.ticker == ticker.upper())
        .order_by(Price.price_date.asc(), Price.source.asc())
    ).all()
    rows = [row for row in rows if row.close is not None]
    if not rows:
        return EventPriceReaction(None, None, None, None, None, None, None, None, None)

    dates = [_parse_date(row.price_date) for row in rows]
    before = [i for i, row_date in enumerate(dates) if row_date < parsed_date]
    event_or_after = [i for i, row_date in enumerate(dates) if row_date >= parsed_date]
    event_exact = [i for i, row_date in enumerate(dates) if row_date == parsed_date]

    prev_idx = before[-1] if before else None
    base_idx = event_or_after[0] if event_or_after else None
    event_volume_idx = event_exact[0] if event_exact else base_idx

    close_prev = rows[prev_idx].close if prev_idx is not None else None
    close_event_or_next = rows[base_idx].close if base_idx is not None else None
    close_plus_1 = (
        rows[base_idx + 1].close if base_idx is not None and base_idx + 1 < len(rows) else None
    )
    close_plus_5 = (
        rows[base_idx + 5].close if base_idx is not None and base_idx + 5 < len(rows) else None
    )
    volume_event = rows[event_volume_idx].volume if event_volume_idx is not None else None

    prior_start = max(0, (base_idx or 0) - 20)
    prior_rows = rows[prior_start : base_idx or 0]
    prior_volumes = [row.volume for row in prior_rows if row.volume is not None]
    avg_volume_20d = sum(prior_volumes) / len(prior_volumes) if prior_volumes else None
    volume_ratio = (
        volume_event / avg_volume_20d
        if volume_event is not None and avg_volume_20d not in (None, 0)
        else None
    )
    return EventPriceReaction(
        close_prev=close_prev,
        close_event_or_next=close_event_or_next,
        close_plus_1=close_plus_1,
        close_plus_5=close_plus_5,
        pct_1d=_pct(close_event_or_next, close_plus_1),
        pct_5d=_pct(close_event_or_next, close_plus_5),
        volume_event=volume_event,
        avg_volume_20d=avg_volume_20d,
        volume_ratio=volume_ratio,
    )
