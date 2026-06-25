from __future__ import annotations

from html.parser import HTMLParser

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.analysis.form4_parser import store_form4_transactions
from market_info_layer.analysis.form8k_parser import store_8k_events
from market_info_layer.db.models import Filing, FilingDocument
from market_info_layer.settings import get_settings
from market_info_layer.utils.rate_limit import sleep_for
from market_info_layer.utils.time import utc_now_iso


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def extract_text(content: str) -> str:
    parser = _TextExtractor()
    parser.feed(content)
    return " ".join(parser.parts) or content


def download_filing_document(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": get_settings().sec_user_agent}, timeout=30)
    response.raise_for_status()
    sleep_for(0.1)
    return response.text


def is_xml_document(url: str, content: str) -> bool:
    return (
        url.lower().endswith(".xml")
        or content.lstrip().startswith("<?xml")
        or "<ownershipDocument" in content
    )


def process_sec_filings(
    session: Session, limit: int = 50, form_type: str | None = None, ticker: str | None = None
) -> int:
    stmt = (
        select(Filing)
        .where(Filing.processed.is_(False))
        .order_by(Filing.filing_date.desc(), Filing.id.desc())
    )
    if form_type:
        stmt = stmt.where(Filing.form_type == form_type)
    if ticker:
        stmt = stmt.where(Filing.ticker == ticker.upper())
    filings = session.scalars(stmt.limit(limit)).all()
    processed = 0
    for filing in filings:
        existing = session.scalar(
            select(FilingDocument.id).where(FilingDocument.filing_id == filing.id)
        )
        if existing:
            continue
        content = download_filing_document(filing.filing_url)
        xml = content if is_xml_document(filing.filing_url, content) else None
        text = extract_text(content)
        session.add(
            FilingDocument(
                filing_id=filing.id,
                ticker=filing.ticker,
                form_type=filing.form_type,
                source_url=filing.filing_url,
                raw_text=text,
                raw_xml=xml,
                downloaded_at=utc_now_iso(),
            )
        )
        if filing.form_type == "4" and xml:
            store_form4_transactions(session, filing.id, xml, filing.filing_url, filing.ticker)
        elif filing.form_type == "8-K":
            store_8k_events(
                session,
                filing.id,
                filing.ticker,
                filing.form_type,
                text,
                filing.filing_url,
                filing.filing_date,
            )
        filing.processed = True
        processed += 1
        session.commit()
    return processed
