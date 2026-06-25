from __future__ import annotations

import re
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from market_info_layer.db.models import FilingEvent
from market_info_layer.utils.time import utc_now_iso

ITEM_TYPES = {
    "1.01": "Material definitive agreement",
    "2.02": "Results of operations / financial condition",
    "2.05": "Exit/disposal activities",
    "2.06": "Material impairments",
    "3.01": "Delisting notice",
    "5.02": "Departure/election of directors or officers",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements/exhibits",
}
HIGH_ITEMS = {"Item 2.05", "Item 2.06", "Item 3.01", "Item 5.02"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def extract_text(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    return re.sub(r"\s+", " ", " ".join(parser.parts) or raw).strip()


def parse_8k_items(raw_text: str) -> list[dict[str, str]]:
    text = extract_text(raw_text)
    pattern = re.compile(
        r"Item\s+([1-9]\.\d{2})\s*[:.\-]?\s*(.*?)(?=\s+Item\s+[1-9]\.\d{2}|$)", re.I
    )
    items: list[dict[str, str]] = []
    for match in pattern.finditer(text):
        number = match.group(1)
        nearby = match.group(2).strip()
        title = ITEM_TYPES.get(number) or nearby[:120] or "Unknown 8-K item"
        items.append(
            {
                "sec_item": f"Item {number}",
                "event_type": ITEM_TYPES.get(number, "Other filing event"),
                "headline": title,
                "summary": nearby[:300] if nearby else title,
            }
        )
    return items


def store_8k_events(
    session: Session,
    filing_id: int,
    ticker: str,
    form_type: str,
    raw_text: str,
    source_url: str,
    event_date: str | None = None,
) -> int:
    items = parse_8k_items(raw_text)
    if not items:
        session.add(
            FilingEvent(
                filing_id=filing_id,
                ticker=ticker,
                form_type=form_type,
                event_date=event_date,
                event_type="Unknown filing event",
                sec_item=None,
                headline="Unparseable 8-K filing",
                summary="No recognized 8-K item headings were detected.",
                importance="unknown",
                source_url=source_url,
                needs_human_review=True,
                created_at=utc_now_iso(),
            )
        )
        return 1
    for item in items:
        session.add(
            FilingEvent(
                filing_id=filing_id,
                ticker=ticker,
                form_type=form_type,
                event_date=event_date,
                event_type=item["event_type"],
                sec_item=item["sec_item"],
                headline=item["headline"],
                summary=item["summary"],
                importance="high" if item["sec_item"] in HIGH_ITEMS else "medium",
                source_url=source_url,
                needs_human_review=False,
                created_at=utc_now_iso(),
            )
        )
    return len(items)
