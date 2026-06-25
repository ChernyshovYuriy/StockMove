from __future__ import annotations

import logging
import re
import time
from html import unescape
from html.parser import HTMLParser

import feedparser
import pandas as pd
import requests
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from market_info_layer.db.models import TradingHalt
from market_info_layer.settings import get_settings
from market_info_layer.utils.time import utc_now_iso

NASDAQ_HALTS_URL = "https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts"
NASDAQ_HALTS_RSS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
MIN_FETCH_INTERVAL_SECONDS = 60
RSS_ACCEPT_HEADER = "application/rss+xml, application/xml, text/xml, */*"
RESPONSE_PREVIEW_CHARS = 500
_LAST_FETCH_MONOTONIC: float | None = None
logger = logging.getLogger(__name__)


class HaltFetchError(RuntimeError):
    pass


class HaltParseError(ValueError):
    pass


class _SimpleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(" ".join(self._current_cell).strip())
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def _tables_from_html(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(html)
    except ImportError:
        parser = _SimpleTableParser()
        parser.feed(html)
        tables = []
        for raw_table in parser.tables:
            if not raw_table:
                continue
            headers, *rows = raw_table
            tables.append(pd.DataFrame(rows, columns=headers))
        return tables
    except ValueError as exc:
        raise HaltParseError("No trading halt table found in Nasdaq response") from exc


def _cell(row, *names: str) -> str | None:
    for name in names:
        value = row.get(name, None)
        if value is not None:
            text = str(value).strip()
            if text and text.lower() != "nan":
                return text
    return None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", unescape(value)).strip()
    return text or None


def _preview_response(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:RESPONSE_PREVIEW_CHARS]


def _looks_like_html(text: str) -> bool:
    stripped = text.lstrip().lower()
    return stripped.startswith(("<!doctype html", "<html")) or "<body" in stripped[:500]


def _validate_halts_response(response: requests.Response) -> str:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise HaltFetchError(
            f"Nasdaq trade halt RSS fetch failed: HTTP {response.status_code}"
        ) from exc

    text = response.text or ""
    content_type = response.headers.get("content-type", "")
    if not text.strip():
        logger.warning("Nasdaq trade halt RSS response was empty; content_type=%s", content_type)
        return ""
    if _looks_like_html(text) or "Oops! That didn't work" in text:
        raise HaltFetchError(
            "Nasdaq trade halt RSS endpoint returned non-RSS content "
            f"(content_type={content_type!r}, preview={_preview_response(text)!r})"
        )
    return text


def _entry_value(entry, *names: str) -> str | None:
    for name in names:
        value = entry.get(name)
        if value:
            if isinstance(value, list):
                value = " ".join(
                    str(v.get("href", v)) if isinstance(v, dict) else str(v) for v in value
                )
            return _clean_text(str(value))
    return None


def _description_text(description: str) -> str:
    parser = _TextExtractor()
    parser.feed(unescape(description))
    return " | ".join(parser.parts) if parser.parts else _clean_text(description) or ""


def _labeled_value(text: str, *labels: str) -> str | None:
    for label in labels:
        match = re.search(
            rf"(?:^|[|\n;])\s*{re.escape(label)}(?!\s*[A-Za-z])\s*:?\s*(.*?)(?=\s*(?:[|\n;]|$))",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return _clean_text(match.group(1))
    return None


def _row_from_entry(entry, collected_at: str) -> dict[str, str | None] | None:
    title = _entry_value(entry, "title") or ""
    description = _entry_value(entry, "summary", "description") or ""
    if "<table" in description.lower():
        try:
            parsed_rows = parse_halts_html(description)
        except HaltParseError as exc:
            logger.warning("Skipping unparseable Nasdaq halt table entry title=%r: %s", title, exc)
        else:
            if parsed_rows:
                return parsed_rows[0] | {
                    "source": NASDAQ_HALTS_RSS_URL,
                    "collected_at": collected_at,
                }

    text_parts = [_description_text(description), title, _entry_value(entry, "links", "link") or ""]
    text = " | ".join(part for part in text_parts if part)
    ticker = _labeled_value(text, "Issue Symbol", "Symbol", "Ticker")
    if ticker is None:
        ticker_match = re.search(r"(?:Trade Halt|Halt)\s*[-:]\s*([A-Z0-9.\-]+)", title, re.I)
        ticker = _clean_text(ticker_match.group(1)) if ticker_match else None
    if not ticker:
        return None
    return {
        "ticker": ticker,
        "halt_time": _labeled_value(text, "Halt Time", "Time")
        or _entry_value(entry, "published", "updated"),
        "resume_time": _labeled_value(text, "Resumption Trade Time", "Resume Time"),
        "reason_code": _labeled_value(text, "Reason Code", "Code"),
        "reason_text": _labeled_value(text, "Reason", "Reason Text"),
        "source": NASDAQ_HALTS_RSS_URL,
        "collected_at": collected_at,
    }


def parse_halts_rss(xml_text: str) -> list[dict[str, str | None]]:
    if not xml_text.strip():
        return []
    feed = feedparser.parse(xml_text)
    entries = list(feed.entries)
    if feed.bozo:
        message = f"Malformed Nasdaq trade halt RSS feed: {_preview_response(xml_text)!r}"
        if not entries:
            raise HaltParseError(message) from getattr(feed, "bozo_exception", None)
        logger.warning(
            "Nasdaq trade halt RSS feed is malformed but contains %s entries; continuing: %s",
            len(entries),
            getattr(feed, "bozo_exception", None),
        )
    if not entries:
        return []

    collected_at = utc_now_iso()
    rows: list[dict[str, str | None]] = []
    for entry in entries:
        try:
            row = _row_from_entry(entry, collected_at)
        except Exception as exc:  # defensive: one bad Nasdaq item should not fail collection
            logger.warning("Skipping unparseable Nasdaq halt RSS entry: %s", exc)
            continue
        if row and row.get("ticker"):
            rows.append(row)
        else:
            logger.warning("Skipping Nasdaq halt RSS entry without parseable ticker")
    return rows


def parse_halts_html(html: str) -> list[dict[str, str | None]]:
    tables = _tables_from_html(html)
    if not tables:
        raise HaltParseError("No trading halt table found in Nasdaq response")
    for table in tables:
        columns = {str(c).strip().lower(): c for c in table.columns}
        symbol_col = next((c for k, c in columns.items() if "symbol" in k), None)
        if symbol_col is None:
            continue
        rows = []
        for _, row in table.iterrows():
            ticker = str(row[symbol_col]).strip()
            if not ticker or ticker.lower() == "nan":
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "halt_time": _cell(row, "Halt Time"),
                    "resume_time": _cell(row, "Resumption Trade Time", "Resume Time"),
                    "reason_code": _cell(row, "Reason Code"),
                    "reason_text": _cell(row, "Reason"),
                    "source": NASDAQ_HALTS_URL,
                    "collected_at": utc_now_iso(),
                }
            )
        return rows
    raise HaltParseError("Nasdaq response did not contain expected symbol column")


def _halt_exists(session: Session, row: dict[str, str | None]) -> bool:
    return (
        session.scalar(
            select(TradingHalt.id).where(
                TradingHalt.ticker == row["ticker"],
                TradingHalt.halt_time == row["halt_time"],
                TradingHalt.reason_code == row["reason_code"],
            )
        )
        is not None
    )


def _can_fetch_now() -> bool:
    global _LAST_FETCH_MONOTONIC
    now = time.monotonic()
    if (
        _LAST_FETCH_MONOTONIC is not None
        and now - _LAST_FETCH_MONOTONIC < MIN_FETCH_INTERVAL_SECONDS
    ):
        return False
    _LAST_FETCH_MONOTONIC = now
    return True


def collect_halts(session: Session) -> int:
    if not _can_fetch_now():
        return 0
    headers = {
        "User-Agent": get_settings().sec_user_agent or "MarketInfoLayer contact@example.com",
        "Accept": RSS_ACCEPT_HEADER,
    }
    response = requests.get(NASDAQ_HALTS_RSS_URL, headers=headers, timeout=30)
    xml_text = _validate_halts_response(response)
    inserted = 0
    for row in parse_halts_rss(xml_text):
        if _halt_exists(session, row):
            continue
        session.add(TradingHalt(**row))
        try:
            session.commit()
            inserted += 1
        except IntegrityError:
            session.rollback()
    return inserted
