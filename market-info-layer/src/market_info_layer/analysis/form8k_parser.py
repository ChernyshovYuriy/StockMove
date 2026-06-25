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

_HIGH_502_ROLE_RE = re.compile(
    r"\b(ceo|chief executive officer|cfo|chief financial officer|coo|chief operating officer|"
    r"president)\b",
    re.I,
)
_HIGH_502_CHANGE_RE = re.compile(
    r"\b(resign(?:ed|ation|s)?|depart(?:ure|ed|ing)?|terminat(?:e|ed|ion)|"
    r"transition(?:s|ed|ing)?(?:\s+(?:from|out of))?|"
    r"effective\s+immediate(?:ly)?|immediate(?:ly)?\s+effect(?:ive)?)\b",
    re.I,
)
_MEDIUM_502_RE = re.compile(
    r"\b(general counsel|principal accounting officer)\b.*"
    r"\b(transition|resign(?:ed|ation|s)?|depart(?:ure|ed|ing)?|appoint(?:ed|ment)?|named)\b|"
    r"\b(transition|resign(?:ed|ation|s)?|depart(?:ure|ed|ing)?|appoint(?:ed|ment)?|named)\b.*"
    r"\b(general counsel|principal accounting officer)\b|"
    r"\b(senior officer|chief accounting officer|chief legal officer|chief operating officer|"
    r"executive vice president|senior vice president|svp|evp)\b.*"
    r"\b(appoint(?:ed|ment)?|named|promoted|elected|transition)\b|"
    r"\b(appoint(?:ed|ment)?|named|promoted|elected|transition)\b.*"
    r"\b(senior officer|chief accounting officer|chief legal officer|chief operating officer|"
    r"executive vice president|senior vice president|svp|evp)\b",
    re.I,
)
_LOW_502_RE = re.compile(
    r"\b(employee stock plan|compensation plan|cash incentive plan|equity incentive plan|"
    r"stock option plan|director appointment only|director appointment|appointed to the board|"
    r"elected to the board|routine board update|committee update|committee assignment|"
    r"committee membership|routine|annual meeting|director election|election of director)\b",
    re.I,
)
_HIGH_202_RE = re.compile(
    r"\b(guidance|outlook|restatement|material weakness|going concern|impairment)\b", re.I
)
_MEDIUM_507_RE = re.compile(
    r"\b(merger agreement|merger|business combination|change of control|"
    r"contested vote|contest(?:ed)? election|proxy contest|activist campaign|"
    r"activist investor|major governance|governance overhaul|special meeting|"
    r"poison pill|shareholder rights plan)\b",
    re.I,
)

SEC_ITEM_HEADER_TITLES = {
    "1.01": r"entry\s+into\s+a\s+material\s+definitive\s+agreement",
    "2.02": r"results\s+of\s+operations\s+and\s+financial\s+condition",
    "2.05": r"costs?\s+associated\s+with\s+exit\s+or\s+disposal\s+activities",
    "2.06": r"material\s+impairments?",
    "3.01": r"notice\s+of\s+delisting|failure\s+to\s+satisfy\s+(?:a\s+)?continued\s+listing",
    "5.02": (
        r"departure\s+of\s+directors?|certain\s+officers?|election\s+of\s+directors?|"
        r"appointment\s+of\s+certain\s+officers?|compensatory\s+arrangements"
    ),
    "5.07": r"submission\s+of\s+matters?\s+to\s+a\s+vote\s+of\s+security\s+holders?",
    "7.01": r"regulation\s+fd\s+disclosure",
    "8.01": r"other\s+events?",
    "9.01": r"financial\s+statements?\s+and\s+exhibits?",
}


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
        if _LOW_502_RE.search(context) and not _HIGH_502_ROLE_RE.search(context):
            return "low", "Routine director, compensation plan, or committee update."
        if _HIGH_502_ROLE_RE.search(context) and _HIGH_502_CHANGE_RE.search(context):
            return (
                "high",
                "Leadership change involving key executives, departure, or immediate effect.",
            )
        if _MEDIUM_502_RE.search(context):
            return "medium", "Senior officer appointment or role change."
        if _LOW_502_RE.search(context):
            return "low", "Routine director, compensation plan, or committee update."
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


def _find_item_headers(text: str) -> list[re.Match[str]]:
    alternatives = "|".join(
        rf"(?P<i{number.replace('.', '_')}>"
        rf"Item\s+{re.escape(number)}(?!\s*\()\s*[:.\-–—]?\s*(?:{title}))"
        for number, title in SEC_ITEM_HEADER_TITLES.items()
    )
    pattern = re.compile(alternatives, re.I)
    matches = list(pattern.finditer(text))
    if not matches:
        leading_item = re.match(r"\s*Item\s+([1-9]\.\d{2})(?!\s*\()\s*[:.\-–—]?", text, re.I)
        if leading_item and leading_item.group(1) in ITEM_TYPES:
            matches = [leading_item]
    return matches


def _header_item_number(match: re.Match[str]) -> str:
    found = re.search(r"Item\s+([1-9]\.\d{2})", match.group(0), re.I)
    if found is None:  # defensive; _find_item_headers only returns Item headers.
        raise ValueError(f"Unable to identify 8-K item header: {match.group(0)!r}")
    return found.group(1)


def parse_8k_items(raw_text: str) -> list[dict[str, str]]:
    text = extract_text(raw_text)
    matches = _find_item_headers(text)
    item_numbers = [_header_item_number(match) for match in matches]
    has_other_events = any(number != "9.01" for number in item_numbers)
    items_by_sec_item: dict[str, dict[str, str]] = {}
    for index, match in enumerate(matches):
        number = _header_item_number(match)
        sec_item = f"Item {number}"
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        nearby = text[match.end() : next_start].strip()
        event_type = ITEM_TYPES.get(number, "Other filing event")
        importance, detail = _classify_item(sec_item, nearby, has_other_events)
        item = {
            "sec_item": sec_item,
            "event_type": event_type,
            "headline": event_type,
            "summary": _event_summary(sec_item, nearby, detail),
            "importance": importance,
        }
        existing = items_by_sec_item.get(sec_item)
        should_replace = (
            existing is None
            or IMPORTANCE_RANK[importance] < IMPORTANCE_RANK[existing["importance"]]
        )
        if should_replace:
            items_by_sec_item[sec_item] = item
    return list(items_by_sec_item.values())


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
