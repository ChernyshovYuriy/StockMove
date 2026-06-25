from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.db.models import (
    Filing,
    FilingEvent,
    InsiderTransaction,
    MacroObservation,
    TradingHalt,
    Watchlist,
)
from market_info_layer.settings import ROOT_DIR


def generate_daily_brief(
    session: Session, brief_date: date | None = None, output_dir: Path | None = None
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
    events = session.scalars(
        select(FilingEvent).where(FilingEvent.event_date == brief_date.isoformat())
    ).all()
    importance_rank = {"high": 0, "medium": 1, "low": 2, "unknown": 3, None: 4}
    sorted_events = sorted(events, key=lambda e: (importance_rank.get(e.importance, 4), e.ticker))
    material_events = [e for e in sorted_events if e.importance in {"high", "medium"}]
    low_events = [e for e in sorted_events if e.importance == "low"]
    insiders = session.scalars(
        select(InsiderTransaction).where(InsiderTransaction.importance.in_(["high", "medium"]))
    ).all()
    review_filings = [
        f for f in filings if not f.processed and f.form_type in {"8-K", "10-K", "10-Q", "S-1"}
    ]
    lines = [
        "# Market Information Layer Daily Brief",
        "",
        f"Date: {brief_date.isoformat()}",
        "",
        "## Known facts",
        "Macro context:",
        *(f"- {m.series_id} {m.observation_date}: {m.value} (source: {m.source})" for m in macros),
        "Interpretation: Not generated in version 1.",
        "Speculation: None.",
        "",
        "## Parsed filing events",
        *(
            f"- [{e.importance}] {e.ticker} {e.sec_item or e.form_type}: "
            f"{e.headline}. {e.summary or ''} Source: {e.source_url}"
            for e in material_events
        ),
        *( ["- No high or medium parsed filing events."] if not material_events else [] ),
        "",
        "## Low-importance parsed filing events",
        *(
            f"- [low] {e.ticker} {e.sec_item or e.form_type}: "
            f"{e.headline}. Source: {e.source_url}"
            for e in low_events
        ),
        *( ["- No low-importance parsed filing events."] if not low_events else [] ),
        "",
        "## Insider transactions",
        *(
            f"- {i.ticker} {i.owner_name}: {i.transaction_type} {i.shares} shares "
            f"at {i.price} on {i.transaction_date} ({i.importance}) {i.source_url}"
            for i in insiders
        ),
        "",
        "## Needs human review",
        *(
            f"- Unprocessed material filing: {f.ticker} {f.form_type} "
            f"filed {f.filing_date}. Source: {f.filing_url}"
            for f in review_filings
        ),
        *(
            ["- No unprocessed material filings found for this date."]
            if not review_filings
            else []
        ),
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
    path = output_dir / f"{brief_date.isoformat()}.md"
    path.write_text("\n".join(lines) + "\n")
    return path
