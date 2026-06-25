from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser

import pandas as pd
import requests
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from market_info_layer.db.models import TradingHalt
from market_info_layer.utils.time import utc_now_iso

NASDAQ_HALTS_URL = "https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts"
NASDAQ_HALTS_RSS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
MIN_FETCH_INTERVAL_SECONDS = 60
_LAST_FETCH_MONOTONIC: float | None = None


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


def _rss_child_text(item: ET.Element, name: str) -> str | None:
    for child in item:
        if child.tag.rsplit("}", 1)[-1].lower() == name.lower():
            return _clean_text(child.text)
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


def parse_halts_rss(xml_text: str) -> list[dict[str, str | None]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise HaltParseError("Malformed Nasdaq trade halt RSS feed") from exc

    items = [elem for elem in root.iter() if elem.tag.rsplit("}", 1)[-1].lower() == "item"]
    if not items:
        return []

    collected_at = utc_now_iso()
    rows: list[dict[str, str | None]] = []
    for item in items:
        title = _rss_child_text(item, "title") or ""
        description = _rss_child_text(item, "description") or ""
        row = None
        if "<table" in description.lower():
            try:
                parsed_rows = parse_halts_html(description)
            except HaltParseError:
                parsed_rows = []
            if parsed_rows:
                row = parsed_rows[0] | {
                    "source": NASDAQ_HALTS_RSS_URL,
                    "collected_at": collected_at,
                }
        if row is None:
            text = _description_text(description)
            ticker = _labeled_value(text, "Issue Symbol", "Symbol", "Ticker")
            if ticker is None:
                ticker_match = re.search(
                    r"(?:Trade Halt|Halt)\s*[-:]\s*([A-Z0-9.\-]+)", title, re.I
                )
                ticker = _clean_text(ticker_match.group(1)) if ticker_match else _clean_text(title)
            row = {
                "ticker": ticker,
                "halt_time": _labeled_value(text, "Halt Time", "Time")
                or _rss_child_text(item, "pubDate"),
                "resume_time": _labeled_value(text, "Resumption Trade Time", "Resume Time"),
                "reason_code": _labeled_value(text, "Reason Code", "Code"),
                "reason_text": _labeled_value(text, "Reason", "Reason Text"),
                "source": NASDAQ_HALTS_RSS_URL,
                "collected_at": collected_at,
            }
        if row.get("ticker"):
            rows.append(row)
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
    response = requests.get(NASDAQ_HALTS_RSS_URL, timeout=30)
    response.raise_for_status()
    inserted = 0
    for row in parse_halts_rss(response.text):
        if _halt_exists(session, row):
            continue
        session.add(TradingHalt(**row))
        try:
            session.commit()
            inserted += 1
        except IntegrityError:
            session.rollback()
    return inserted
