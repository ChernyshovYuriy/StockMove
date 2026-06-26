# ruff: noqa: E501, E701
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import requests
from requests import RequestException
from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.analysis.form4_parser import Form4ParseError, store_form4_transactions
from market_info_layer.analysis.form8k_parser import store_8k_events
from market_info_layer.analysis.sec_filing_parser import store_generic_filing_events
from market_info_layer.db.models import Filing, FilingDocument, FilingEvent
from market_info_layer.settings import get_settings
from market_info_layer.utils.rate_limit import sleep_for
from market_info_layer.utils.time import utc_now_iso

SUPPORTED_PROCESSING_FORMS = {"8-K", "4", "10-K", "10-Q", "DEF 14A", "S-1", "SC 13G"}

_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "caption",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
}
_IGNORED_TAGS = {"script", "style", "meta", "head"}
_XBRL_METADATA_PREFIXES = ("dei:", "link:", "xbrli:", "xbrldi:", "xlink:", "xsd:")
_XBRL_METADATA_TAGS = {
    "context",
    "continuation",
    "exclude",
    "footnote",
    "header",
    "hidden",
    "metadata",
    "references",
    "relationship",
    "resources",
    "schemaRef",
    "unit",
}
_NOISE_VALUES = {"true", "false", "yes", "no", "dei", "iso4217", "shares", "usd"}
_TRADING_VENUES = {"nasdaq", "nyse", "amex", "arca", "cboe", "otc", "otcqb", "otcqx"}
_MEANINGFUL_FILING_MARKER_RE = re.compile(
    r"\b(?:UNITED\s+STATES\s+SECURITIES\s+AND\s+EXCHANGE\s+COMMISSION|"
    r"FORM\s+8-K|CURRENT\s+REPORT|Item\s+[1-9]\.\d{2}|SIGNATURES?)\b",
    re.I,
)
_CIK_RE = re.compile(r"\b\d{10}\b")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_ACCESSIONISH_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")


class _SecHtmlTextExtractor(HTMLParser):
    """Extract visible filing prose while ignoring inline-XBRL boilerplate."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_stack: list[str] = []
        self._seen_body = False
        self._has_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        attrs_dict = {name.lower(): value for name, value in attrs}
        if normalized == "body":
            self._seen_body = True
            self._has_body = True
        if self._should_ignore_start(normalized, attrs_dict):
            self._ignored_stack.append(normalized)
            return
        if normalized in _BLOCK_TAGS:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if self._ignored_stack and self._ignored_stack[-1] == normalized:
            self._ignored_stack.pop()
            return
        if not self._ignored_stack and normalized in _BLOCK_TAGS:
            self._append_break()

    def handle_data(self, data: str) -> None:
        if self._ignored_stack:
            return
        if self._has_body and not self._seen_body:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.parts.append(text)

    def _append_break(self) -> None:
        if self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def _should_ignore_start(self, tag: str, attrs: dict[str, str | None]) -> bool:
        local_name = tag.rsplit(":", 1)[-1]
        if tag in _IGNORED_TAGS:
            return True
        if tag.startswith(_XBRL_METADATA_PREFIXES):
            return True
        if tag.startswith("ix:") and local_name in _XBRL_METADATA_TAGS:
            return True
        if local_name in _XBRL_METADATA_TAGS and tag.startswith(("ix", "xbrl")):
            return True
        style = (attrs.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            return True
        hidden_attr = attrs.get("hidden")
        aria_hidden = (attrs.get("aria-hidden") or "").lower()
        input_type = (attrs.get("type") or "").lower()
        class_name = (attrs.get("class") or "").lower()
        return (
            hidden_attr is not None
            or aria_hidden == "true"
            or input_type == "hidden"
            or "hidden" in class_name.split()
        )


class _PlainTextExtractor(HTMLParser):
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


def _dedupe_noise_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen_noise: set[str] = set()
    for line in lines:
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact:
            continue
        normalized = compact.strip(" :;,.|\u00a0").lower()
        is_short_noise = normalized in _NOISE_VALUES or normalized in _TRADING_VENUES
        is_namespace_noise = bool(
            re.fullmatch(
                r"(?:[a-z][\w.-]*:)?(?:[a-z][\w.-]*)(?:member|axis|domain|abstract)?",
                normalized,
            )
            and (":" in normalized or normalized.endswith(("member", "axis", "domain", "abstract")))
            and len(normalized) < 80
        )
        is_repeated_ticker = bool(re.fullmatch(r"[A-Z]{1,5}", compact))
        if is_short_noise or is_namespace_noise or is_repeated_ticker:
            if normalized in seen_noise:
                continue
            seen_noise.add(normalized)
            if is_short_noise or is_namespace_noise:
                continue
        cleaned.append(compact)
    return cleaned


def _trim_to_meaningful_filing_text(text: str) -> str:
    """Drop leading inline-XBRL cover-page fact runs before the visible filing body."""
    match = _MEANINGFUL_FILING_MARKER_RE.search(text)
    if match is None:
        return text
    prefix = text[: match.start()]
    prefix_tokens = re.findall(r"[A-Za-z0-9_.:-]+", prefix)
    if not prefix_tokens:
        return text[match.start() :]
    technical_tokens = 0
    for token in prefix_tokens:
        normalized = token.strip(" :;,.|\u00a0").lower()
        if (
            normalized in _NOISE_VALUES
            or normalized in _TRADING_VENUES
            or _CIK_RE.fullmatch(token)
            or _DATE_RE.fullmatch(token)
            or _ACCESSIONISH_RE.fullmatch(token)
            or ":" in token
            or re.fullmatch(r"[a-z]{1,6}-?\d{8}", normalized)
        ):
            technical_tokens += 1
    if technical_tokens / max(len(prefix_tokens), 1) >= 0.45 or len(prefix) < 2000:
        return text[match.start() :]
    return text


def _normalize_extracted_text(text: str) -> str:
    text = re.sub(r"\b(?:true|false)(?:\s+(?:true|false)){1,}\b", " ", text, flags=re.I)
    text = re.sub(r"\b\d{10}(?:\s+\d{10}){1,}\b", " ", text)
    venue_pattern = "|".join(sorted(_TRADING_VENUES, key=len, reverse=True))
    text = re.sub(rf"\b({venue_pattern})(?:\s+\1){{1,}}\b", " ", text, flags=re.I)
    text = re.sub(r"\b[a-z][\w.-]*:[A-Za-z0-9_.-]+\b", " ", text)
    text = re.sub(r"\b[A-Za-z][A-Za-z0-9]*(?:Member|Axis|Domain|Abstract)\b", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def extract_text(content: str) -> str:
    parser = _SecHtmlTextExtractor() if _looks_like_html(content) else _PlainTextExtractor()
    parser.feed(content)
    extracted = "\n".join(" ".join(parser.parts).split("\n")) if parser.parts else content
    normalized = _normalize_extracted_text(extracted)
    trimmed = _trim_to_meaningful_filing_text(normalized)
    lines = _dedupe_noise_lines(trimmed.splitlines())
    return _normalize_extracted_text("\n".join(lines)) or content


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
        if filing.form_type not in SUPPORTED_PROCESSING_FORMS:
            filing.processed = True
            filing.processing_status = "unsupported_type"
            session.commit()
            processed += 1
            continue
        existing = session.scalar(
            select(FilingDocument.id).where(FilingDocument.filing_id == filing.id)
        )
        if existing:
            continue
        try:
            download = _coerce_download(download_filing_document(filing.filing_url), filing.filing_url)
        except RequestException:
            if filing.form_type in {"10-K", "10-Q", "DEF 14A", "S-1", "SC 13G"}:
                filing.processed = True
                filing.processing_status = "unsupported_type"
                session.commit()
                processed += 1
                continue
            raise
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
                raw_html=content if _looks_like_html(content, download.content_type) else None,
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
                filing.processing_status = "parsed"
            except Form4ParseError as exc:
                _record_unparseable_form4(session, filing, download.final_url, str(exc))
                filing.processing_status = "parser_failed"
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
            filing.processing_status = "parsed"
        elif filing.form_type in {"10-K", "10-Q", "DEF 14A", "S-1", "SC 13G"}:
            store_generic_filing_events(session, filing.id, filing.ticker, filing.form_type, text, download.final_url, filing.filing_date)
            filing.processing_status = "parsed"
        filing.processed = True
        processed += 1
        session.commit()
    return processed
