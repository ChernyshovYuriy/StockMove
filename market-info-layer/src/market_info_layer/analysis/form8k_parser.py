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
    "5.07": "Shareholder voting results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements/exhibits",
}

HIGH_DEFAULT_ITEMS = {"Item 2.05", "Item 2.06", "Item 3.01"}
IMPORTANCE_RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3}

_HIGH_502_RE = re.compile(
    r"\b(ceo|chief executive officer|cfo|chief financial officer|coo|chief operating officer|"
    r"president|resign(?:ed|ation|s)?|depart(?:ure|ed|ing)?|terminat(?:e|ed|ion)|"
    r"immediate(?:ly)?\s+effect(?:ive)?|effective\s+immediate(?:ly)?)\b",
    re.I,
)
_MEDIUM_502_RE = re.compile(
    r"\b(appoint(?:ed|ment)?|named|promoted|elected)\b.*\b("
    r"chief|officer|senior vice president|executive vice president|svp|evp|general counsel)\b|"
    r"\b(chief|officer|senior vice president|executive vice president|svp|evp|general counsel)\b.*"
    r"\b(appoint(?:ed|ment)?|named|promoted|elected)\b",
    re.I,
)
_LOW_502_RE = re.compile(
    r"\b(routine|annual meeting|director election|election of director|elected to the board|"
    r"board of directors|compensation plan|equity incentive plan|stock option plan|"
    r"committee update|committee assignment|committee membership)\b",
    re.I,
)
_HIGH_202_RE = re.compile(
    r"\b(guidance|outlook|restatement|material weakness|going concern|impairment)\b", re.I
)
_MEDIUM_507_RE = re.compile(
    r"\b(merger|acquisition|activist|contested vote|contest(?:ed)? election|proxy contest|"
    r"major governance|governance issue)\b",
    re.I,
)


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


def _clean_snippet(text: str, limit: int = 300) -> str:
    snippet = re.sub(r"\s+", " ", text).strip(" :-—–\t\n\r")
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 1].rsplit(" ", 1)[0] + "…"


def _classify_item(sec_item: str, context: str, has_other_events: bool) -> tuple[str, str]:
    title = sec_item.replace("Item ", "")
    if sec_item == "Item 5.02":
        if _HIGH_502_RE.search(context):
            return (
                "high",
                "Leadership change involving key executives, departure, or immediate effect.",
            )
        if _LOW_502_RE.search(context):
            return "low", "Routine director, compensation plan, or committee update."
        if _MEDIUM_502_RE.search(context):
            return "medium", "Senior officer appointment or role change."
        return "medium", "Director or officer change disclosed; review context."
    if sec_item == "Item 9.01":
        detail = (
            "Supporting exhibit information for another 8-K event."
            if has_other_events
            else "Exhibit or financial statement information."
        )
        return "low", detail
    if sec_item == "Item 2.02":
        if _HIGH_202_RE.search(context):
            return (
                "high",
                "Results disclosure includes guidance, outlook, restatement, weakness, "
                "going concern, or impairment language.",
            )
        return "medium", "Results of operations or financial condition update."
    if sec_item == "Item 5.07":
        if _MEDIUM_507_RE.search(context):
            return (
                "medium",
                "Shareholder vote involves M&A, activism, a contested vote, or major "
                "governance issue.",
            )
        return "low", "Routine shareholder or annual meeting voting results."
    if sec_item in HIGH_DEFAULT_ITEMS:
        return "high", f"{ITEM_TYPES.get(title, '8-K event')} disclosed."
    return "medium", f"{ITEM_TYPES.get(title, '8-K event')} disclosed."


def _event_summary(sec_item: str, context: str, detail: str) -> str:
    snippet = _clean_snippet(context)
    if snippet:
        return f"{detail} Context: {snippet}"
    return detail


def parse_8k_items(raw_text: str) -> list[dict[str, str]]:
    text = extract_text(raw_text)
    pattern = re.compile(
        r"Item\s+([1-9]\.\d{2})\s*[:.\-]?\s*(.*?)(?=\s+Item\s+[1-9]\.\d{2}|$)", re.I
    )
    matches = list(pattern.finditer(text))
    item_numbers = [match.group(1) for match in matches]
    has_other_events = any(number != "9.01" for number in item_numbers)
    items: list[dict[str, str]] = []
    for match in matches:
        number = match.group(1)
        sec_item = f"Item {number}"
        nearby = match.group(2).strip()
        event_type = ITEM_TYPES.get(number, "Other filing event")
        importance, detail = _classify_item(sec_item, nearby, has_other_events)
        items.append(
            {
                "sec_item": sec_item,
                "event_type": event_type,
                "headline": event_type,
                "summary": _event_summary(sec_item, nearby, detail),
                "importance": importance,
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
                importance=item["importance"],
                source_url=source_url,
                needs_human_review=False,
                created_at=utc_now_iso(),
            )
        )
    return len(items)
