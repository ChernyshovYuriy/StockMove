from html.parser import HTMLParser

import pandas as pd
import requests
from sqlalchemy.orm import Session

from market_info_layer.db.models import TradingHalt
from market_info_layer.utils.time import utc_now_iso

NASDAQ_HALTS_URL = "https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts"


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
                    "halt_time": str(row.get("Halt Time", "")) or None,
                    "resume_time": str(row.get("Resumption Trade Time", "")) or None,
                    "reason_code": str(row.get("Reason Code", "")) or None,
                    "reason_text": str(row.get("Reason", "")) or None,
                    "source": NASDAQ_HALTS_URL,
                    "collected_at": utc_now_iso(),
                }
            )
        return rows
    raise HaltParseError("Nasdaq response did not contain expected symbol column")


def collect_halts(session: Session) -> int:
    response = requests.get(NASDAQ_HALTS_URL, timeout=30)
    response.raise_for_status()
    rows = parse_halts_html(response.text)
    for row in rows:
        session.add(TradingHalt(**row))
    session.commit()
    return len(rows)
