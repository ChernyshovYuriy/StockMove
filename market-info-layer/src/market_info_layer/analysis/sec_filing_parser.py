# ruff: noqa: E501, E701
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.analysis.event_hash import deterministic_event_hash
from market_info_layer.analysis.form8k_parser import extract_text
from market_info_layer.db.models import FilingEvent
from market_info_layer.utils.time import utc_now_iso

_KEYWORDS = [("risk_factor_update","Risk factor update",r"risk factors?|material risks?","medium"),("revenue_growth_profitability","Revenue/growth/profitability mention",r"revenue|growth|profitab","medium"),("liquidity_capital_resources","Liquidity/capital resources",r"liquidity|capital resources|cash flow","medium"),("going_concern","Going concern",r"going concern","high"),("material_weakness","Material weakness",r"material weakness|internal control","high"),("legal_proceedings","Legal proceedings",r"legal proceedings|litigation|lawsuit","medium"),("guidance_outlook","Guidance/outlook",r"guidance|outlook|forecast","medium")]
_PROXY = [("annual_meeting","Annual meeting",r"annual meeting|special meeting","medium"),("board_nominees","Board nominees",r"nominees?|election of directors?","medium"),("executive_compensation","Executive compensation",r"executive compensation|compensation discussion","medium"),("say_on_pay","Say-on-pay",r"say[- ]on[- ]pay|advisory vote","medium"),("auditor_ratification","Auditor ratification",r"ratif(?:y|ication).*auditor|independent registered public accounting","low"),("shareholder_proposal","Shareholder proposal",r"shareholder proposal|stockholder proposal","medium")]
_S1 = [("registration_statement","Registration statement",r"registration statement|form s-1","medium"),("offering_size","Offering size",r"proposed maximum aggregate offering price|offering size|gross proceeds","medium"),("use_of_proceeds","Use of proceeds",r"use of proceeds","medium"),("risk_factor_update","Risk factors",r"risk factors?","medium"),("business_overview","Business summary",r"business overview|our business|company overview","low")]


def _snippet(text: str, pattern: str, limit: int = 220) -> str:
    match = re.search(pattern, text, re.I)
    if not match:
        return ""
    return re.sub(r"\s+", " ", text[max(0, match.start()-80):min(len(text), match.end()+140)]).strip()[:limit]


def _add_event(session: Session, **kwargs) -> int:
    kwargs.setdefault("event_hash", deterministic_event_hash(**kwargs))
    existing = session.scalar(select(FilingEvent.id).where(FilingEvent.event_hash == kwargs["event_hash"]))
    if existing:
        return 0
    session.add(FilingEvent(created_at=utc_now_iso(), needs_human_review=False, **kwargs))
    return 1


def store_generic_filing_events(session: Session, filing_id: int, ticker: str, form_type: str, raw_text: str, source_url: str, event_date: str | None = None) -> int:
    text = extract_text(raw_text or "")
    rules = _KEYWORDS if form_type in {"10-K", "10-Q"} else _PROXY if form_type == "DEF 14A" else _S1 if form_type == "S-1" else []
    inserted = 0
    if form_type == "SC 13G":
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        shares = re.search(r"([\d,]+)\s+shares?\s+beneficially", text, re.I)
        person = re.search(r"(?:reporting person|name of reporting person)[:\s]+([^\n]{3,80})", text, re.I)
        parts = ["Beneficial ownership disclosure"]
        if person: parts.append(f"reporting person {person.group(1).strip()}")
        if pct: parts.append(f"percent owned {pct.group(1)}%")
        if shares: parts.append(f"shares {shares.group(1)}")
        return _add_event(session, filing_id=filing_id, ticker=ticker, form_type=form_type, event_date=event_date, event_type="beneficial_ownership_disclosure", sec_item=None, headline="Beneficial ownership disclosure", summary="; ".join(parts), importance="medium", source_url=source_url)
    for event_type, headline, pattern, importance in rules:
        if re.search(pattern, text, re.I):
            inserted += _add_event(session, filing_id=filing_id, ticker=ticker, form_type=form_type, event_date=event_date, event_type=event_type, sec_item=None, headline=headline, summary=f"{headline} detected. Context: {_snippet(text, pattern)}", importance=importance, source_url=source_url)
    if inserted == 0:
        inserted += _add_event(session, filing_id=filing_id, ticker=ticker, form_type=form_type, event_date=event_date, event_type="parsed_filing", sec_item=None, headline=f"Parsed {form_type}", summary=f"{form_type} processed; no configured high-signal keywords detected.", importance="low", source_url=source_url)
    return inserted
