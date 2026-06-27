# ruff: noqa: E501, E701
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.analysis.event_hash import deterministic_event_hash
from market_info_layer.analysis.form8k_parser import extract_text
from market_info_layer.db.models import FilingEvent
from market_info_layer.utils.time import utc_now_iso

_KEYWORDS = [("liquidity_capital_resources","Liquidity/capital resources",r"liquidity|capital resources|cash flow","medium"),("legal_proceedings","Legal proceedings",r"legal proceedings|litigation|lawsuit","medium"),("guidance_outlook","Guidance/outlook",r"guidance|outlook|forecast","medium")]
_PROXY = [("annual_meeting","Annual meeting",r"annual meeting|special meeting","medium"),("board_nominees","Board nominees",r"nominees?|election of directors?","medium"),("executive_compensation","Executive compensation",r"executive compensation|compensation discussion","medium"),("say_on_pay","Say-on-pay",r"say[- ]on[- ]pay|advisory vote","medium"),("auditor_ratification","Auditor ratification",r"ratif(?:y|ication).*auditor|independent registered public accounting","low"),("shareholder_proposal","Shareholder proposal",r"shareholder proposal|stockholder proposal","medium")]
_S1 = [("registration_statement","Registration statement",r"registration statement|form s-1","medium"),("offering_size","Offering size",r"proposed maximum aggregate offering price|offering size|gross proceeds","medium"),("use_of_proceeds","Use of proceeds",r"use of proceeds","medium"),("risk_factor_update","Risk factors",r"risk factors?","medium"),("business_overview","Business summary",r"business overview|our business|company overview","low")]


_BOILERPLATE_SECTION_RE = re.compile(r"item\s+1a\.\s*risk factors\s*$|table of contents|signatures?|exhibit index|forward-looking statements?|xbrl", re.I)
_NEGATION_RE = re.compile(r"no material weaknesses?|no changes in (?:the )?(?:company.s )?internal control|not identified any material weakness|did not identify(?: any)? material weakness|no substantial doubt|does not raise substantial doubt", re.I)
_MATERIAL_WEAKNESS_NEGATIVE_RE = re.compile(
    r"assessing the risk that a material weakness exists|reasonable assurance|maintained effective internal control|effective internal control over financial reporting|in all material respects|no material weaknesses?|did not identify(?: any)? material weakness|no change in internal control|no changes in (?:the )?(?:company.s )?internal control|unqualified opinion",
    re.I,
)
_MATERIAL_WEAKNESS_POSITIVE_RE = re.compile(
    r"identified (?:a )?material weakness|disclosed (?:a )?material weakness|material weakness(?:es)? (?:exists|existed|were identified|was identified)|internal control over financial reporting was not effective|management concluded.{0,80}internal control.{0,40}ineffective|remediation of material weakness",
    re.I,
)
_THIRD_PARTY_GOING_CONCERN_RE = re.compile(r"(?:customers?|vendors?|suppliers?|partners?|counterparties|tenants|borrowers|third parties|other parties).{0,80}going concern", re.I)

def _clean_analysis_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        if not normalized or _BOILERPLATE_SECTION_RE.fullmatch(normalized):
            continue
        lines.append(normalized)
    return "\n".join(lines)

def _section_name(context_before: str) -> str | None:
    matches = list(re.finditer(r"item\s+([0-9]+[a-z]?)\.\s*([^\n]{0,80})", context_before, re.I))
    if not matches:
        return None
    m = matches[-1]
    return re.sub(r"\s+", " ", m.group(0)).strip()

def _evidence(text: str, match: re.Match[str], rule: str, negation_checked: bool = True) -> str:
    start = max(0, match.start() - 160)
    end = min(len(text), match.end() + 220)
    context = re.sub(r"\s+", " ", text[start:end]).strip()
    section = _section_name(text[max(0, match.start() - 2000):match.start()]) or "unknown"
    return (
        f"matched_phrase={match.group(0)!r}; section={section}; rule={rule}; "
        f"negation_filter_checked={negation_checked}; context: {context}"
    )

def _detect_generic_event(text: str, event_type: str) -> tuple[re.Match[str], str] | None:
    if event_type == "material_weakness":
        for m in re.finditer(r"material weakness(?:es)?|internal control over financial reporting", text, re.I):
            window = text[max(0, m.start() - 180): min(len(text), m.end() + 240)]
            if _MATERIAL_WEAKNESS_NEGATIVE_RE.search(window) or _NEGATION_RE.search(window):
                continue
            positive = _MATERIAL_WEAKNESS_POSITIVE_RE.search(window)
            if positive:
                return positive, "material_weakness_positive_disclosure"
    if event_type == "going_concern":
        for m in re.finditer(r"substantial doubt.{0,120}going concern|going concern", text, re.I):
            window = text[max(0, m.start() - 180): min(len(text), m.end() + 220)]
            if _NEGATION_RE.search(window) or _THIRD_PARTY_GOING_CONCERN_RE.search(window):
                continue
            if re.search(r"(?:company|registrant|we|our).{0,120}(?:ability to continue as a going concern|going concern)|conditions raise substantial doubt", window, re.I):
                return m, "going_concern_filer_substantial_doubt"
    if event_type == "risk_factor_update":
        for m in re.finditer(r"(?:material changes?.{0,80}risk factors?|risk factors?.{0,80}(?:changed|updated|materially modified)|materially modified.{0,80}risk factors?)", text, re.I):
            return m, "risk_factor_change_disclosure"
    if event_type == "revenue_growth_profitability":
        scrubbed = re.sub(r"emerging growth company", "", text, flags=re.I)
        m = re.search(r"revenue|profitab|growth (?!company)", scrubbed, re.I)
        if m:
            return m, "performance_keyword_non_status"
    return None

def _structured_rules(form_type: str) -> list[tuple[str, str, str]]:
    if form_type in {"10-K", "10-Q"}:
        return [
            ("material_weakness", "Material weakness", "high"),
            ("going_concern", "Going concern", "high"),
            ("risk_factor_update", "Risk factor update", "medium"),
            ("revenue_growth_profitability", "Revenue/growth/profitability mention", "medium"),
        ]
    return []


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
    analysis_text = _clean_analysis_text(text)
    for event_type, headline, importance in _structured_rules(form_type):
        detected = _detect_generic_event(analysis_text, event_type)
        if detected:
            match, rule = detected
            inserted += _add_event(session, filing_id=filing_id, ticker=ticker, form_type=form_type, event_date=event_date, event_type=event_type, sec_item=None, headline=headline, summary=f"{headline} detected. Evidence: {_evidence(analysis_text, match, rule)}", importance=importance, source_url=source_url)
    for event_type, headline, pattern, importance in rules:
        if re.search(pattern, analysis_text, re.I):
            inserted += _add_event(session, filing_id=filing_id, ticker=ticker, form_type=form_type, event_date=event_date, event_type=event_type, sec_item=None, headline=headline, summary=f"{headline} detected. Evidence: matched_phrase={pattern!r}; section=unknown; rule=generic_keyword; negation_filter_checked=False; context: {_snippet(analysis_text, pattern)}", importance=importance, source_url=source_url)
    if inserted == 0:
        inserted += _add_event(session, filing_id=filing_id, ticker=ticker, form_type=form_type, event_date=event_date, event_type="parsed_filing", sec_item=None, headline=f"Parsed {form_type}", summary=f"{form_type} processed; no configured high-signal keywords detected.", importance="low", source_url=source_url)
    return inserted
