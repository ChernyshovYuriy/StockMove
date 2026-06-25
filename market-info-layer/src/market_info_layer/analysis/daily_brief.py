from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.db.models import Filing, MacroObservation, TradingHalt, Watchlist
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
    lines = [
        "# Market Information Layer Daily Brief",
        "",
        f"Date: {brief_date.isoformat()}",
        "",
        "## Macro context",
        "Known facts:",
        *(f"- {m.series_id} {m.observation_date}: {m.value} (source: {m.source})" for m in macros),
        "Interpretation: Not generated in version 1.",
        "Speculation: None.",
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
        "## Watchlist review",
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
