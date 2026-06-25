import pandas as pd
import requests
from sqlalchemy.orm import Session

from market_info_layer.db.models import TradingHalt
from market_info_layer.utils.time import utc_now_iso

NASDAQ_HALTS_URL = "https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts"


class HaltParseError(ValueError):
    pass


def parse_halts_html(html: str) -> list[dict[str, str | None]]:
    try:
        tables = pd.read_html(html)
    except ValueError as exc:
        raise HaltParseError("No trading halt table found in Nasdaq response") from exc
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
