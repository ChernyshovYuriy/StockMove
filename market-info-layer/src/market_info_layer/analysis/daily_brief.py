from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.analysis.price_context import event_price_reaction
from market_info_layer.collectors.fred_macro import latest_macro_values
from market_info_layer.db.models import (
    Filing,
    FilingDocument,
    FilingEvent,
    InsiderTransaction,
    TradingHalt,
    Watchlist,
)
from market_info_layer.settings import ROOT_DIR

MATERIAL_FILING_TYPES = {"8-K", "10-Q", "10-K", "S-1", "424B", "SC 13D", "SC 13G", "DEF 14A"}
IMPORTANCE_RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3, None: 4}
ReportStyle = Literal["compact", "debug"]
DEFAULT_MAX_UNPROCESSED = 10


def _iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _event_sort_key(event: FilingEvent) -> tuple[int, int, str]:
    event_ordinal = (_iso_date(event.event_date) or date.min).toordinal()
    return (IMPORTANCE_RANK.get(event.importance, 4), -event_ordinal, event.ticker)


def _truncate(value: str, max_chars: int) -> str:
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _event_summary(event: FilingEvent) -> str:
    summary_parts = [part for part in (event.headline, event.summary) if part]
    return _truncate(". ".join(summary_parts) if summary_parts else "No summary available.", 240)


def _format_price_context(session: Session, event: FilingEvent, *, debug: bool = False) -> str:
    if not event.event_date:
        return "Price reaction around event: unavailable (missing event date; needs human review)."
    reaction = event_price_reaction(session, event.ticker, event.event_date)
    if reaction.status == "missing_price_data" or reaction.close_event_or_next is None:
        return "Price reaction around event: unavailable (missing_price_data; needs human review)."
    if not debug and reaction.status == "incomplete_price_window":
        return (
            "Price reaction around event: unavailable "
            "(incomplete_price_window; needs human review)."
        )
    def fmt(value):
        return "n/a" if value is None else f"{value:.2f}"
    def fmti(value):
        return "n/a" if value is None else str(value)
    if not debug:
        return (
            "Price context: "
            f"+1d {fmt(reaction.pct_1d)}%, +5d {fmt(reaction.pct_5d)}%, "
            f"volume {fmt(reaction.volume_ratio)}x 20d avg. "
            "Price reaction around event; same-period movement; needs human review."
        )
    return (
        "Price reaction around event (same-period movement; needs human review): "
        f"status={reaction.status}, "
        f"close_prev={fmt(reaction.close_prev)}, "
        f"close_event_or_next={fmt(reaction.close_event_or_next)}, "
        f"close_plus_1={fmt(reaction.close_plus_1)}, pct_1d={fmt(reaction.pct_1d)}%, "
        f"close_plus_5={fmt(reaction.close_plus_5)}, pct_5d={fmt(reaction.pct_5d)}%, "
        f"volume_event={fmti(reaction.volume_event)}, "
        f"avg_volume_20d={fmt(reaction.avg_volume_20d)}, "
        f"volume_ratio={fmt(reaction.volume_ratio)}"
    )


def _format_event_debug(session: Session, event: FilingEvent) -> str:
    return (
        f"- [{event.importance or 'unknown'}] {event.ticker} {event.sec_item or event.form_type}: "
        f"event_date={event.event_date or 'unknown'} form_type={event.form_type} "
        f"sec_item={event.sec_item or 'n/a'} event_type={event.event_type or 'n/a'} "
        f"needs_human_review={event.needs_human_review}: "
        f"{event.headline or 'No headline'}. {event.summary or ''} "
        f"{_format_price_context(session, event, debug=True)} Source: {event.source_url}"
    )


def _format_event_compact(
    session: Session, event: FilingEvent, *, include_price_context: bool
) -> str:
    price_context = (
        "\n" + _format_price_context(session, event) if include_price_context else ""
    )
    return (
        f"[{event.importance or 'unknown'}] {event.ticker} — "
        f"{event.event_date or 'unknown'} — {event.sec_item or event.form_type or 'n/a'}\n"
        f"Event: {event.event_type or 'n/a'}\n"
        f"Summary: {_event_summary(event)}\n"
        f"Source: {event.source_url}"
        f"{price_context}"
    )


def _format_event(
    session: Session,
    event: FilingEvent,
    style: ReportStyle,
    *,
    include_low: bool = False,
    debug_price_context: bool = False,
) -> str:
    if style == "debug":
        return _format_event_debug(session, event)
    include_price_context = (
        event.importance in {"high", "medium"}
        and not _is_item_901(event)
        or (event.importance == "low" and include_low and debug_price_context)
    )
    include_price_context = include_price_context and not _is_item_901(event)
    return _format_event_compact(
        session, event, include_price_context=include_price_context
    )


def _format_macro_latest(row: dict) -> str:
    name = f" ({row['name']})" if row.get("name") else ""
    observation_date = row.get("observation_date") or "No observation recorded"
    collected_at = row.get("collected_at") or "n/a"
    return (
        f"- {row['series_id']}{name}: "
        f"{observation_date} value={row.get('value')} collected_at={collected_at}"
    )


def _is_item_901(event: FilingEvent) -> bool:
    return (event.sec_item or "").strip().lower() == "item 9.01"


def _select_events(
    session: Session,
    brief_date: date,
    lookback_days: int | None,
    processed_today: bool,
) -> list[FilingEvent]:
    events = session.scalars(select(FilingEvent)).all()
    if processed_today:
        downloaded_today_filing_ids = {
            document.filing_id
            for document in session.scalars(select(FilingDocument)).all()
            if _iso_date(document.downloaded_at) == brief_date
        }
        return [
            event
            for event in events
            if _iso_date(event.created_at) == brief_date
            or event.filing_id in downloaded_today_filing_ids
        ]
    if lookback_days is not None:
        start_date = brief_date - timedelta(days=lookback_days)
        return [
            event
            for event in events
            if (event_date := _iso_date(event.event_date)) is not None
            and start_date <= event_date <= brief_date
        ]
    return [event for event in events if event.event_date == brief_date.isoformat()]


def generate_daily_brief(
    session: Session,
    brief_date: date | None = None,
    output_dir: Path | None = None,
    *,
    lookback_days: int | None = None,
    processed_today: bool = False,
    include_low: bool = False,
    output_name: str | None = None,
    style: ReportStyle = "compact",
    max_unprocessed: int = DEFAULT_MAX_UNPROCESSED,
    debug_price_context: bool = False,
) -> Path:
    if style not in ("compact", "debug"):
        raise ValueError("style must be compact or debug")
    if max_unprocessed < 0:
        raise ValueError("max_unprocessed must be non-negative")
    brief_date = brief_date or datetime.now(UTC).date()
    output_dir = output_dir or ROOT_DIR / "reports" / "daily"
    output_dir.mkdir(parents=True, exist_ok=True)
    watch = session.scalars(select(Watchlist)).all()
    tickers = {w.ticker for w in watch}
    filings = session.scalars(
        select(Filing).where(Filing.filing_date == brief_date.isoformat())
    ).all()
    halts = [h for h in session.scalars(select(TradingHalt)).all() if h.ticker in tickers]
    all_halts = session.scalars(
        select(TradingHalt).order_by(TradingHalt.halt_datetime.desc())
    ).all()
    macros = latest_macro_values(session)
    selected_events = _select_events(session, brief_date, lookback_days, processed_today)
    sorted_events = sorted(selected_events, key=_event_sort_key)
    visible_events = [
        e for e in sorted_events if style == "debug" or include_low or not _is_item_901(e)
    ]
    material_events = [e for e in visible_events if include_low or e.importance != "low"]
    low_events = [] if include_low else [e for e in visible_events if e.importance == "low"]
    # Avoid emitting identical filing rows in both parsed and recently processed sections.
    # The parsed sections remain the canonical event listing; this section is only
    # populated for future distinct processed-only records.
    processed_events = []
    insider_importance = ["high", "medium", "low"] if include_low else ["high", "medium"]
    insiders = session.scalars(
        select(InsiderTransaction)
        .where(InsiderTransaction.importance.in_(insider_importance))
        .order_by(InsiderTransaction.transaction_date.desc(), InsiderTransaction.id.desc())
    ).all()
    review_filings = session.scalars(
        select(Filing)
        .where(Filing.processed.is_(False), Filing.form_type.in_(MATERIAL_FILING_TYPES))
        .order_by(Filing.filing_date.desc(), Filing.id.desc())
        .limit(max_unprocessed + 1)
    ).all()
    unprocessed_more_count = max(0, len(review_filings) - max_unprocessed)
    review_filings = review_filings[:max_unprocessed]
    selection_text = f"event_date={brief_date.isoformat()}"
    if lookback_days is not None:
        selection_text = (
            f"event_date between {(brief_date - timedelta(days=lookback_days)).isoformat()} "
            f"and {brief_date.isoformat()}"
        )
    if processed_today:
        selection_text = f"created_at or downloaded_at date={brief_date.isoformat()}"
    lines = [
        "# Market Information Layer Daily Brief",
        "",
        f"Date: {brief_date.isoformat()}",
        f"Report style: {style}",
        f"Parsed filing event selection: {selection_text}",
        "",
        "## Known facts",
        "## Macro Context",
        *(_format_macro_latest(m) for m in macros),
        "Interpretation: Not generated in version 1.",
        "Speculation: None.",
        "",
        "## Parsed filing events",
        *(
            _format_event(
                session,
                e,
                style,
                include_low=include_low,
                debug_price_context=debug_price_context,
            )
            for e in material_events
        ),
        *(["- No parsed filing events for this selection."] if not material_events else []),
        "",
        "## Low-importance parsed filing events",
        *(
            _format_event(
                session,
                e,
                style,
                include_low=include_low,
                debug_price_context=debug_price_context,
            )
            for e in low_events
        ),
        *(["- No low-importance parsed filing events."] if not low_events else []),
        "",
        "## Recently processed filing events",
        *(
            _format_event(
                session,
                e,
                style,
                include_low=include_low,
                debug_price_context=debug_price_context,
            )
            for e in processed_events
        ),
        *(
            ["- Not requested. Use --processed-today to populate this section."]
            if not processed_today
            else []
        ),
        *(
            ["- No filing events were created on the report date."]
            if processed_today and not processed_events
            else []
        ),
        "",
        "## Insider transactions",
        *(
            f"- {i.ticker} {i.owner_name}: {i.transaction_type} {i.shares} shares "
            f"at {i.price} on {i.transaction_date} ({i.importance}) {i.source_url}"
            for i in insiders
        ),
        "",
        "## Unprocessed material filings",
        *(
            f"- {f.ticker} {f.form_type} filed {f.filing_date}. Source: {f.filing_url}"
            for f in review_filings
        ),
        *(["- No unprocessed material filings found."] if not review_filings else []),
        *(
            [
                f"- {unprocessed_more_count} more unprocessed material filings not shown. "
                "Use --max-unprocessed to adjust."
            ]
            if unprocessed_more_count
            else []
        ),
        "",
        "## Needs human review",
        *(
            f"- Unprocessed material filing: {f.ticker} {f.form_type} "
            f"filed {f.filing_date}. Source: {f.filing_url}"
            for f in review_filings
        ),
        *(["- No unprocessed material filings found."] if not review_filings else []),
        *(
            [
                f"- {unprocessed_more_count} more unprocessed material filings not shown. "
                "Use --max-unprocessed to adjust."
            ]
            if unprocessed_more_count
            else []
        ),
        "",
        "## New SEC filings for watchlist tickers",
        "Known facts:",
        *(f"- {f.ticker} {f.form_type} filed {f.filing_date}: {f.filing_url}" for f in filings),
        "Human review needed: Review material filings manually.",
        "",
        "## Trading Halts",
        "Known facts:",
        *(
            f"- {h.halt_datetime} {h.ticker} reason_code={h.reason_code} "
            f"reason_text={h.reason_text} resume={h.resume_datetime}"
            for h in halts
        ),
        *(["No trading halts recorded for watchlist tickers."] if not halts else []),
        *(
            ["", "### All collected trading halts (debug)"]
            + [
                f"- {h.halt_datetime} {h.ticker} reason_code={h.reason_code} "
                f"reason_text={h.reason_text} resume={h.resume_datetime}"
                for h in all_halts
            ]
            if style == "debug"
            else []
        ),
        "",
        "## Watchlist implications",
        "Known facts:",
        *(f"- {w.ticker}: status={w.status}, confidence={w.confidence}" for w in watch),
        "Interpretation: Human-maintained thesis fields remain separate from raw facts.",
        "",
        "## Items requiring human review",
        "- New filings, data anomalies, and thesis updates.",
        "",
        "## Open questions",
        "- Add questions during manual review.",
        "",
        "## Notes for postmortem",
        "- Add notes after market close.",
    ]
    filename = output_name or brief_date.isoformat()
    path = output_dir / f"{filename}.md"
    path.write_text("\n".join(lines) + "\n")
    return path
