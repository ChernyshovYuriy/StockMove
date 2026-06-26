import subprocess
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import typer
import yaml
from sqlalchemy.orm import Session

from market_info_layer.analysis.daily_brief import DEFAULT_MAX_UNPROCESSED, generate_daily_brief
from market_info_layer.collectors.fred_macro import collect_macro_observations
from market_info_layer.collectors.nasdaq_halts import collect_halts
from market_info_layer.collectors.prices import collect_prices
from market_info_layer.collectors.sec_documents import process_sec_filings
from market_info_layer.collectors.sec_edgar import collect_sec_filings
from market_info_layer.db.database import get_engine
from market_info_layer.db.database import init_db as create_db
from market_info_layer.db.models import Ticker, Watchlist
from market_info_layer.debug_export import create_debug_export
from market_info_layer.settings import ROOT_DIR
from market_info_layer.utils.time import utc_now_iso

app = typer.Typer()


def session() -> Session:
    return Session(get_engine())


@app.command("init-db")
def init_db_command() -> None:
    create_db()
    typer.echo("Database initialized")


@app.command("load-watchlist")
def load_watchlist(path: Path = ROOT_DIR / "config" / "watchlist.yaml") -> None:
    create_db()
    data = yaml.safe_load(path.read_text()) or {}
    with session() as s:
        for item in data.get("tickers", []):
            ticker = item["ticker"].upper()
            s.merge(
                Ticker(
                    ticker=ticker,
                    company_name=item.get("company_name"),
                    cik=str(item.get("cik")) if item.get("cik") else None,
                    sector=item.get("sector"),
                    industry=item.get("industry"),
                    active=item.get("active", True),
                )
            )
            existing = s.query(Watchlist).filter_by(ticker=ticker).one_or_none()
            values = dict(
                ticker=ticker,
                reason_watching=item.get("reason_watching"),
                thesis=item.get("thesis"),
                invalidation_condition=item.get("invalidation_condition"),
                catalyst=item.get("catalyst"),
                next_known_date=item.get("next_known_date"),
                confidence=item.get("confidence"),
                status=item.get("status"),
                updated_at=utc_now_iso(),
            )
            if existing:
                for k, v in values.items():
                    setattr(existing, k, v)
            else:
                s.add(Watchlist(**values))
        s.commit()
    typer.echo("Watchlist loaded")


@app.command("collect-sec")
def collect_sec() -> None:
    create_db()
    with session() as s:
        typer.echo(f"Inserted {collect_sec_filings(s)} SEC filings")


@app.command("process-sec-filings")
def process_sec_filings_command(
    limit: int = typer.Option(50, "--limit"),
    form_type: str | None = typer.Option(None, "--form-type"),
    ticker: str | None = typer.Option(None, "--ticker"),
) -> None:
    create_db()
    with session() as s:
        count = process_sec_filings(s, limit=limit, form_type=form_type, ticker=ticker)
        typer.echo(f"Processed {count} SEC filings")


@app.command("collect-macro")
def collect_macro() -> None:
    create_db()
    with session() as s:
        typer.echo(f"Inserted {collect_macro_observations(s)} observations")


@app.command("collect-halts")
def collect_halts_command() -> None:
    create_db()
    with session() as s:
        typer.echo(f"Inserted {collect_halts(s)} trading halts")


@app.command("collect-prices")
def collect_prices_command(
    ticker: Annotated[str | None, typer.Option("--ticker")] = None,
    period: Annotated[str, typer.Option("--period")] = "2y",
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    include_current_day: Annotated[bool, typer.Option("--include-current-day")] = False,
) -> None:
    create_db()
    with session() as s:
        count = collect_prices(
            s,
            ticker=ticker,
            period=period,
            start=start,
            end=end,
            include_current_day=include_current_day,
        )
        typer.echo(f"Inserted {count} prices")


@app.command("sec-routine")
def sec_routine(
    limit_per_form: Annotated[int, typer.Option("--limit-per-form", min=1)] = 500,
) -> None:
    """Run the daily SEC metadata and document-processing routine."""
    create_db()
    with session() as s:
        inserted = collect_sec_filings(s)
        processed_8k = process_sec_filings(s, limit=limit_per_form, form_type="8-K")
        processed_form4 = process_sec_filings(s, limit=limit_per_form, form_type="4")
    typer.echo(
        "SEC routine complete: "
        f"inserted {inserted} filing metadata rows; "
        f"processed {processed_8k} 8-K filings; "
        f"processed {processed_form4} Form 4 filings"
    )


@app.command("collect-all")
def collect_all() -> None:
    sec_routine()
    collect_macro()
    collect_halts_command()
    collect_prices_command()


@app.command("daily-brief")
def daily_brief(
    brief_date: Annotated[str | None, typer.Option("--date")] = None,
    lookback_days: Annotated[int | None, typer.Option("--lookback-days", min=0)] = None,
    processed_today: Annotated[bool, typer.Option("--processed-today")] = False,
    include_low: Annotated[bool, typer.Option("--include-low")] = False,
    output_name: Annotated[str | None, typer.Option("--output-name")] = None,
    style: Annotated[Literal["compact", "debug"], typer.Option("--style")] = "compact",
    max_unprocessed: Annotated[
        int, typer.Option("--max-unprocessed", min=0)
    ] = DEFAULT_MAX_UNPROCESSED,
    debug_price_context: Annotated[bool, typer.Option("--debug-price-context")] = False,
) -> None:
    create_db()
    parsed_date = date.fromisoformat(brief_date) if brief_date else None
    with session() as s:
        typer.echo(
            generate_daily_brief(
                s,
                brief_date=parsed_date,
                lookback_days=lookback_days,
                processed_today=processed_today,
                include_low=include_low,
                output_name=output_name,
                style=style,
                max_unprocessed=max_unprocessed,
                debug_price_context=debug_price_context,
            )
        )


@app.command("backfill-review")
def backfill_review(
    brief_date: Annotated[str | None, typer.Option("--date")] = None,
    include_low: Annotated[bool, typer.Option("--include-low")] = True,
    output_name: Annotated[str | None, typer.Option("--output-name")] = "backfill-review",
) -> None:
    create_db()
    parsed_date = date.fromisoformat(brief_date) if brief_date else None
    with session() as s:
        typer.echo(
            generate_daily_brief(
                s,
                brief_date=parsed_date,
                processed_today=True,
                include_low=include_low,
                output_name=output_name,
            )
        )


@app.command("export-debug")
def export_debug(
    output_dir: Annotated[Path, typer.Option("--output-dir")] = ROOT_DIR / "export",
    include_db: Annotated[bool, typer.Option("--include-db")] = False,
    include_raw_documents: Annotated[bool, typer.Option("--include-raw-documents")] = False,
    limit_rows_per_table: Annotated[int, typer.Option("--limit-rows-per-table", min=1)] = 10_000,
) -> None:
    try:
        zip_path = create_debug_export(
            output_dir=output_dir,
            include_db=include_db,
            include_raw_documents=include_raw_documents,
            limit_rows_per_table=limit_rows_per_table,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(zip_path)


@app.command("dashboard")
def dashboard() -> None:
    subprocess.run(
        ["streamlit", "run", str(ROOT_DIR / "src/market_info_layer/dashboard/streamlit_app.py")],
        check=False,
    )


if __name__ == "__main__":
    app()
