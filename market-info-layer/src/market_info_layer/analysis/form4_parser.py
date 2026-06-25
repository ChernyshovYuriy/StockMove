from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from sqlalchemy.orm import Session

from market_info_layer.db.models import InsiderTransaction
from market_info_layer.utils.time import utc_now_iso


class Form4ParseError(ValueError):
    """Raised when a Form 4 document is not parseable ownership XML."""


def _looks_like_html(content: str) -> bool:
    head = content.lstrip()[:500].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<body" in head


def _looks_like_sec_error_page(content: str) -> bool:
    head = content.lstrip()[:4000].lower()
    error_markers = (
        "request rate threshold exceeded",
        "sec.gov request rate threshold exceeded",
        "your request rate has exceeded",
        "access denied",
        "temporarily unavailable",
        "service unavailable",
        "too many requests",
    )
    return any(marker in head for marker in error_markers)


def _validate_form4_xml_shape(raw_xml: str) -> None:
    if not raw_xml or not raw_xml.strip():
        raise Form4ParseError("empty Form 4 document")
    if _looks_like_html(raw_xml):
        raise Form4ParseError("document is HTML, not raw Form 4 XML")
    if _looks_like_sec_error_page(raw_xml):
        raise Form4ParseError("SEC error or rate-limit response, not Form 4 XML")
    lowered = raw_xml[:10000].lower()
    if "<ownershipdocument" not in lowered:
        raise Form4ParseError("document does not look like Form 4 ownership XML")


CODE_TYPES = {
    "P": "Purchase",
    "S": "Sale",
    "A": "Grant/Award",
    "M": "Option exercise/conversion",
    "F": "Tax withholding/payment",
    "G": "Gift",
}


def _txt(node: ET.Element | None, path: str) -> str | None:
    found = node.find(path) if node is not None else None
    return found.text.strip() if found is not None and found.text else None


def _num(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _role(owner: ET.Element | None) -> str | None:
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    if rel is None:
        return None
    roles: list[str] = []
    if _txt(rel, "isDirector") in {"1", "true", "True"}:
        roles.append("Director")
    if _txt(rel, "isOfficer") in {"1", "true", "True"}:
        title = _txt(rel, "officerTitle")
        roles.append(f"Officer ({title})" if title else "Officer")
    if _txt(rel, "isTenPercentOwner") in {"1", "true", "True"}:
        roles.append("10% Owner")
    if _txt(rel, "isOther") in {"1", "true", "True"}:
        roles.append(_txt(rel, "otherText") or "Other")
    return ", ".join(roles) if roles else None


def classify_transaction(code: str | None, shares: float | None) -> str:
    if code == "P":
        return "high"
    if code == "S":
        return "high" if (shares or 0) >= 100_000 else "medium"
    if code == "M":
        return "medium"
    if code in {"A", "F", "G"}:
        return "low"
    return "unknown"


@dataclass
class ParsedForm4Transaction:
    ticker: str | None
    owner_name: str | None
    owner_role: str | None
    transaction_date: str | None
    transaction_code: str | None
    transaction_type: str | None
    shares: float | None
    price: float | None
    direct_or_indirect: str | None
    shares_owned_after: float | None
    importance: str


def parse_form4_xml(raw_xml: str) -> list[ParsedForm4Transaction]:
    _validate_form4_xml_shape(raw_xml)
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise Form4ParseError(f"malformed Form 4 XML: {exc}") from exc
    if root.tag != "ownershipDocument":
        raise Form4ParseError(f"unexpected Form 4 XML root: {root.tag}")
    ticker = _txt(root, "issuer/issuerTradingSymbol")
    owner = root.find("reportingOwner")
    owner_name = _txt(owner, "reportingOwnerId/rptOwnerName")
    owner_role = _role(owner)
    parsed: list[ParsedForm4Transaction] = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        code = _txt(txn, "transactionCoding/transactionCode")
        shares = _num(_txt(txn, "transactionAmounts/transactionShares/value"))
        parsed.append(
            ParsedForm4Transaction(
                ticker,
                owner_name,
                owner_role,
                _txt(txn, "transactionDate/value"),
                code,
                CODE_TYPES.get(code or "", "Unknown"),
                shares,
                _num(_txt(txn, "transactionAmounts/transactionPricePerShare/value")),
                _txt(txn, "ownershipNature/directOrIndirectOwnership/value"),
                _num(_txt(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
                classify_transaction(code, shares),
            )
        )
    return parsed


def store_form4_transactions(
    session: Session,
    filing_id: int,
    raw_xml: str,
    source_url: str,
    fallback_ticker: str | None = None,
) -> int:
    rows = parse_form4_xml(raw_xml)
    for row in rows:
        session.add(
            InsiderTransaction(
                filing_id=filing_id,
                ticker=(row.ticker or fallback_ticker or "").upper(),
                owner_name=row.owner_name,
                owner_role=row.owner_role,
                transaction_date=row.transaction_date,
                transaction_code=row.transaction_code,
                transaction_type=row.transaction_type,
                shares=row.shares,
                price=row.price,
                direct_or_indirect=row.direct_or_indirect,
                shares_owned_after=row.shares_owned_after,
                source_url=source_url,
                collected_at=utc_now_iso(),
                importance=row.importance,
            )
        )
    return len(rows)
