# ruff: noqa: E501, E701
import logging
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
logger = logging.getLogger(__name__)


def pad_cik(cik: str | int) -> str:
    return str(cik).strip().zfill(10)


def raw_primary_document(form_type: str, primary_document: str | None) -> str | None:
    """Return the archive document path that should be downloaded.

    SEC Form 4 recent filings sometimes advertise a rendered xslF345X.. path as
    the primary document. The raw ownership XML lives in the same accession
    directory under the basename, so avoid storing/downloading the transformed
    view.
    """
    if form_type == "4" and primary_document and "xslf345" in primary_document.lower():
        return primary_document.rsplit("/", 1)[-1]
    return primary_document


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
        primary_doc = raw_primary_document(form_type, (recent.get("primaryDocument") or [None])[i])
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
    requested_forms = sorted(FORMS)
    for item in read_watchlist(watchlist_path):
        ticker = item.get("ticker")
        if item.get("active") is False:
            logger.info("Skipping inactive SEC collection ticker %s", ticker)
            continue
        cik = item.get("cik")
        if not cik or not str(cik).strip().isdigit():
            logger.warning("Skipping SEC collection for %s: no CIK mapping or invalid CIK", ticker)
            continue
        fetched = inserted_for_ticker = skipped = errors = 0
        try:
            payload = fetch_submissions(cik)
            rows = parse_recent_filings(ticker, cik, payload)
            fetched = len(rows)
            if not rows:
                logger.info("No supported SEC filings found for %s", ticker)
            for row in rows:
                exists = session.scalar(select(Filing.id).where(Filing.accession_number == row["accession_number"]))
                if exists:
                    skipped += 1
                    continue
                row.setdefault("processing_status", "discovered")
                session.add(Filing(**row))
                inserted += 1
                inserted_for_ticker += 1
            session.commit()
        except Exception:
            session.rollback()
            errors += 1
            logger.exception("SEC collection failed for %s CIK %s", ticker, cik)
        finally:
            logger.info("SEC ingestion summary ticker=%s cik=%s requested_forms=%s fetched=%s inserted=%s skipped_existing=%s errors=%s", ticker, pad_cik(cik), requested_forms, fetched, inserted_for_ticker, skipped, errors)
        time.sleep(delay_seconds)
    return inserted
