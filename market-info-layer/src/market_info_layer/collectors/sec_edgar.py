import time
from pathlib import Path
from typing import Any

import requests
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.db.models import Filing
from market_info_layer.settings import ROOT_DIR, get_settings
from market_info_layer.utils.time import utc_now_iso

FORMS = {"8-K", "10-Q", "10-K", "S-1", "424B", "DEF 14A", "4", "SC 13D", "SC 13G"}
SEC_SOURCE = "SEC EDGAR submissions"


def pad_cik(cik: str | int) -> str:
    return str(cik).strip().zfill(10)


def filing_url(cik: str, accession_number: str, primary_document: str | None) -> str:
    compact = accession_number.replace("-", "")
    doc = primary_document or ""
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{doc}"


def read_watchlist(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or ROOT_DIR / "config" / "watchlist.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("tickers", [])


def fetch_submissions(cik: str, user_agent: str | None = None) -> dict[str, Any]:
    padded = pad_cik(cik)
    headers = {"User-Agent": user_agent or get_settings().sec_user_agent}
    response = requests.get(
        f"https://data.sec.gov/submissions/CIK{padded}.json", headers=headers, timeout=30
    )
    response.raise_for_status()
    return response.json()


def parse_recent_filings(ticker: str, cik: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    recent = payload.get("filings", {}).get("recent", {})
    rows = []
    for i, form_type in enumerate(recent.get("form", [])):
        if form_type not in FORMS:
            continue
        accession = recent.get("accessionNumber", [])[i]
        primary_doc = (recent.get("primaryDocument") or [None])[i]
        rows.append(
            {
                "ticker": ticker.upper(),
                "cik": pad_cik(cik),
                "form_type": form_type,
                "filing_date": (recent.get("filingDate") or [None])[i],
                "report_date": (recent.get("reportDate") or [None])[i],
                "accession_number": accession,
                "primary_document": primary_doc,
                "filing_url": filing_url(cik, accession, primary_doc),
                "source": SEC_SOURCE,
                "collected_at": utc_now_iso(),
            }
        )
    return rows


def collect_sec_filings(
    session: Session, watchlist_path: Path | None = None, delay_seconds: float = 0.1
) -> int:
    inserted = 0
    for item in read_watchlist(watchlist_path):
        if not item.get("cik"):
            continue
        payload = fetch_submissions(item["cik"])
        for row in parse_recent_filings(item["ticker"], item["cik"], payload):
            exists = session.scalar(
                select(Filing.id).where(Filing.accession_number == row["accession_number"])
            )
            if exists:
                continue
            session.add(Filing(**row))
            inserted += 1
        session.commit()
        time.sleep(delay_seconds)
    return inserted
