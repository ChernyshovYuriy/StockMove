from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.analysis.form4_parser import Form4ParseError, store_form4_transactions
from market_info_layer.analysis.form8k_parser import store_8k_events
from market_info_layer.db.models import Filing, FilingDocument, FilingEvent
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


@dataclass
class DownloadedFilingDocument:
    text: str
    final_url: str
    content_type: str | None
    status_code: int


def extract_text(content: str) -> str:
    parser = _TextExtractor()
    parser.feed(content)
    return " ".join(parser.parts) or content


def download_filing_document(url: str) -> DownloadedFilingDocument:
    response = requests.get(url, headers={"User-Agent": get_settings().sec_user_agent}, timeout=30)
    sleep_for(0.1)
    return DownloadedFilingDocument(
        text=response.text,
        final_url=response.url,
        content_type=response.headers.get("Content-Type"),
        status_code=response.status_code,
    )


def _coerce_download(
    download: DownloadedFilingDocument | str, source_url: str
) -> DownloadedFilingDocument:
    if isinstance(download, str):
        return DownloadedFilingDocument(
            text=download, final_url=source_url, content_type=None, status_code=200
        )
    return download


def _looks_like_html(content: str, content_type: str | None = None) -> bool:
    if content_type and "html" in content_type.lower():
        return True
    head = content.lstrip()[:500].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<body" in head


def _looks_like_sec_error_page(content: str) -> bool:
    head = content.lstrip()[:4000].lower()
    return any(
        marker in head
        for marker in (
            "request rate threshold exceeded",
            "sec.gov request rate threshold exceeded",
            "your request rate has exceeded",
            "access denied",
            "temporarily unavailable",
            "service unavailable",
            "too many requests",
        )
    )


def _extract_ownership_xml(content: str) -> str | None:
    lower = content.lower()
    start = lower.find("<ownershipdocument")
    if start == -1:
        return None
    end_tag = "</ownershipdocument>"
    end = lower.find(end_tag, start)
    if end == -1:
        return content[start:]
    return content[start : end + len(end_tag)]


def form4_xml_candidate(
    url: str, content: str, content_type: str | None = None, primary_document: str | None = None
) -> str | None:
    if not content or not content.strip():
        return None
    if _looks_like_sec_error_page(content) or _looks_like_html(content, content_type):
        return None
    extracted = _extract_ownership_xml(content)
    if extracted:
        return extracted
    if primary_document and "xslf345" in primary_document.lower():
        return None
    if url.lower().endswith(".xml") or content.lstrip().startswith("<?xml"):
        return content
    return None


def is_xml_document(url: str, content: str) -> bool:
    return form4_xml_candidate(url, content) is not None


def _record_unparseable_form4(
    session: Session, filing: Filing, source_url: str, reason: str
) -> None:
    session.add(
        FilingEvent(
            filing_id=filing.id,
            ticker=filing.ticker,
            form_type=filing.form_type,
            event_date=filing.filing_date,
            event_type="Unparseable Form 4",
            sec_item=None,
            headline="Unparseable Form 4",
            summary=f"Form 4 could not be parsed: {reason}",
            importance="unknown",
            source_url=source_url,
            needs_human_review=True,
            created_at=utc_now_iso(),
        )
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
        download = _coerce_download(download_filing_document(filing.filing_url), filing.filing_url)
        content = download.text
        text = extract_text(content)
        xml = (
            form4_xml_candidate(
                download.final_url, content, download.content_type, filing.primary_document
            )
            if filing.form_type == "4"
            else content
            if is_xml_document(download.final_url, content)
            else None
        )
        session.add(
            FilingDocument(
                filing_id=filing.id,
                ticker=filing.ticker,
                form_type=filing.form_type,
                source_url=download.final_url,
                raw_text=text,
                raw_xml=xml,
                downloaded_at=utc_now_iso(),
                http_status_code=download.status_code,
                content_type=download.content_type,
            )
        )
        if filing.form_type == "4":
            try:
                if download.status_code < 200 or download.status_code >= 300:
                    raise Form4ParseError(f"HTTP status {download.status_code}")
                if not xml:
                    raise Form4ParseError("downloaded document is not raw Form 4 ownership XML")
                store_form4_transactions(session, filing.id, xml, download.final_url, filing.ticker)
            except Form4ParseError as exc:
                _record_unparseable_form4(session, filing, download.final_url, str(exc))
        elif filing.form_type == "8-K":
            store_8k_events(
                session,
                filing.id,
                filing.ticker,
                filing.form_type,
                text,
                download.final_url,
                filing.filing_date,
            )
        filing.processed = True
        processed += 1
        session.commit()
    return processed
