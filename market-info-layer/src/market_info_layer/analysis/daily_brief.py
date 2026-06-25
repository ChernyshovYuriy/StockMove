from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.db.models import (
    Filing,
    FilingDocument,
    FilingEvent,
    InsiderTransaction,
    MacroObservation,
    TradingHalt,
    Watchlist,
)
from market_info_layer.settings import ROOT_DIR

MATERIAL_FILING_TYPES = {"8-K", "10-Q", "10-K", "S-1", "424B", "SC 13D", "SC 13G", "DEF 14A"}
IMPORTANCE_RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3, None: 4}


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


def _format_event(event: FilingEvent) -> str:
    return (
        f"- [{event.importance or 'unknown'}] {event.ticker} {event.sec_item or event.form_type}: "
        f"event_date={event.event_date or 'unknown'} form_type={event.form_type} "
        f"sec_item={event.sec_item or 'n/a'} event_type={event.event_type or 'n/a'} "
        f"needs_human_review={event.needs_human_review}: "
        f"{event.headline or 'No headline'}. {event.summary or ''} Source: {event.source_url}"
    )


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
) -> Path:
    brief_date = brief_date or datetime.now(UTC).date()
    output_dir = output_dir or ROOT_DIR / "reports" / "daily"
    output_dir.mkdir(parents=True, exist_ok=True)
    watch = session.scalars(select(Watchlist)).all()
    tickers = {w.ticker for w in watch}
    filings = session.scalars(
        select(Filing).where(Filing.filing_date == brief_date.isoformat())
    ).all()
    halts = [h for h in session.scalars(select(TradingHalt)).all() if h.ticker in tickers]
    macros = session.scalars(
        select(MacroObservation).order_by(MacroObservation.observation_date.desc()).limit(10)
    ).all()
    selected_events = _select_events(session, brief_date, lookback_days, processed_today)
    sorted_events = sorted(selected_events, key=_event_sort_key)
    material_events = [e for e in sorted_events if include_low or e.importance != "low"]
    low_events = [] if include_low else [e for e in sorted_events if e.importance == "low"]
    processed_events = sorted_events if processed_today else []
    insiders = session.scalars(
        select(InsiderTransaction).where(InsiderTransaction.importance.in_(["high", "medium"]))
    ).all()
    review_filings = session.scalars(
        select(Filing)
        .where(Filing.processed.is_(False), Filing.form_type.in_(MATERIAL_FILING_TYPES))
        .order_by(Filing.filing_date.desc(), Filing.id.desc())
        .limit(20)
    ).all()
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
        f"Parsed filing event selection: {selection_text}",
        "",
        "## Known facts",
        "Macro context:",
        *(f"- {m.series_id} {m.observation_date}: {m.value} (source: {m.source})" for m in macros),
        "Interpretation: Not generated in version 1.",
        "Speculation: None.",
        "",
        "## Parsed filing events",
        *(_format_event(e) for e in material_events),
        *(["- No parsed filing events for this selection."] if not material_events else []),
        "",
        "## Low-importance parsed filing events",
        *(_format_event(e) for e in low_events),
        *(["- No low-importance parsed filing events."] if not low_events else []),
        "",
        "## Recently processed filing events",
        *(_format_event(e) for e in processed_events),
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
        "",
        "## Needs human review",
        *(
            f"- Unprocessed material filing: {f.ticker} {f.form_type} "
            f"filed {f.filing_date}. Source: {f.filing_url}"
            for f in review_filings
        ),
        *(["- No unprocessed material filings found."] if not review_filings else []),
        "",
        "## New SEC filings for watchlist tickers",
        "Known facts:",
        *(f"- {f.ticker} {f.form_type} filed {f.filing_date}: {f.filing_url}" for f in filings),
        "Human review needed: Review material filings manually.",
        "",
        "## Trading halts affecting watchlist tickers",
        "Known facts:",
        *(
            f"- {h.ticker} halt={h.halt_time} resume={h.resume_time} "
            f"reason={h.reason_code} {h.reason_text}"
            for h in halts
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
