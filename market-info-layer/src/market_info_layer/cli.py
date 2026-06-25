import subprocess
from pathlib import Path

import typer
import yaml
from sqlalchemy.orm import Session

from market_info_layer.analysis.daily_brief import generate_daily_brief
from market_info_layer.collectors.fred_macro import collect_macro_observations
from market_info_layer.collectors.nasdaq_halts import collect_halts
from market_info_layer.collectors.sec_edgar import collect_sec_filings
from market_info_layer.db.database import get_engine
from market_info_layer.db.database import init_db as create_db
from market_info_layer.db.models import Ticker, Watchlist
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


@app.command("collect-macro")
def collect_macro() -> None:
    create_db()
    with session() as s:
        typer.echo(f"Inserted {collect_macro_observations(s)} observations")


@app.command("collect-halts")
def collect_halts_command() -> None:
    create_db()
    with session() as s:
        typer.echo(f"Inserted {collect_halts(s)} halts")


@app.command("collect-all")
def collect_all() -> None:
    collect_sec()
    collect_macro()
    collect_halts_command()


@app.command("daily-brief")
def daily_brief() -> None:
    create_db()
    with session() as s:
        typer.echo(generate_daily_brief(s))


@app.command("dashboard")
def dashboard() -> None:
    subprocess.run(
        ["streamlit", "run", str(ROOT_DIR / "src/market_info_layer/dashboard/streamlit_app.py")],
        check=False,
    )


if __name__ == "__main__":
    app()
