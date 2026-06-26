# ruff: noqa: E501, E701
from __future__ import annotations

import hashlib
import json
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
    "D": "Disposition to issuer",
    "F": "Tax withholding/payment",
    "G": "Gift",
    "M": "Option exercise/conversion",
    "C": "Conversion",
    "X": "Option exercise",
    "J": "Other acquisition/disposition",
}

CODE_DETAILS = {
    "P": "Open market or private purchase of non-derivative or derivative securities.",
    "S": "Open market or private sale of non-derivative or derivative securities.",
    "A": "Grant, award, or other acquisition from issuer.",
    "D": "Sale or other disposition back to issuer.",
    "F": "Payment of exercise price or tax liability by delivering or withholding securities.",
    "G": "Bona fide gift of securities.",
    "M": "Exercise or conversion of derivative security.",
    "C": "Conversion of derivative security.",
    "X": "Exercise of in-the-money or out-of-the-money derivative security.",
    "J": "Other acquisition or disposition described in transaction footnotes.",
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


def classify_transaction(code: str | None, shares: float | None = None) -> str:
    del shares
    if code == "P":
        return "high"
    if code == "S":
        return "medium"
    if code in {"A", "F", "M", "G", "D", "C", "X", "J"}:
        return "low"
    return "unknown"


@dataclass
class ParsedForm4Transaction:
    issuer_ticker: str | None
    issuer_name: str | None
    reporting_owner_name: str | None
    reporting_owner_cik: str | None
    relationship_to_issuer: str | None
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
    security_title: str | None = None
    transaction_table: str | None = None
    transaction_row_index: int | None = None
    footnote_ids: str | None = None
    ownership_form: str | None = None
    deemed_execution_date: str | None = None

    @property
    def ticker(self) -> str | None:
        return self.issuer_ticker


def parse_form4_xml(raw_xml: str) -> list[ParsedForm4Transaction]:
    _validate_form4_xml_shape(raw_xml)
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise Form4ParseError(f"malformed Form 4 XML: {exc}") from exc
    if root.tag != "ownershipDocument":
        raise Form4ParseError(f"unexpected Form 4 XML root: {root.tag}")
    issuer_ticker = _txt(root, "issuer/issuerTradingSymbol")
    issuer_name = _txt(root, "issuer/issuerName")
    owner = root.find("reportingOwner")
    owner_name = _txt(owner, "reportingOwnerId/rptOwnerName")
    owner_cik = _txt(owner, "reportingOwnerId/rptOwnerCik")
    owner_role = _role(owner)
    parsed: list[ParsedForm4Transaction] = []
    transaction_nodes = [("non_derivative", i, n) for i, n in enumerate(root.findall(".//nonDerivativeTransaction"), start=1)] + [("derivative", i, n) for i, n in enumerate(root.findall(".//derivativeTransaction"), start=1)]
    for table_name, row_index, txn in transaction_nodes:
        code = _txt(txn, "transactionCoding/transactionCode")
        shares = _num(_txt(txn, "transactionAmounts/transactionShares/value"))
        parsed.append(
            ParsedForm4Transaction(
                issuer_ticker,
                issuer_name,
                owner_name,
                owner_cik,
                owner_role,
                owner_name,
                owner_role,
                _txt(txn, "transactionDate/value"),
                code,
                CODE_TYPES.get(code or "", f"Unknown transaction code: {code or ''}"),
                shares,
                _num(_txt(txn, "transactionAmounts/transactionPricePerShare/value")),
                _txt(txn, "ownershipNature/directOrIndirectOwnership/value"),
                _num(_txt(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
                classify_transaction(code, shares),
                _txt(txn, "securityTitle/value"),
                table_name,
                row_index,
                ",".join([el.attrib.get("id", "") for el in txn.findall(".//footnoteId") if el.attrib.get("id")]) or None,
                _txt(txn, "ownershipNature/directOrIndirectOwnership/value"),
                _txt(txn, "deemedExecutionDate/value"),
            )
        )
    return parsed


def _transaction_hash(filing_id: int, row: ParsedForm4Transaction) -> str:
    payload = [filing_id, row.transaction_table, row.transaction_row_index, row.security_title, row.transaction_date, row.transaction_code, row.shares, row.price, row.direct_or_indirect]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

def _transaction_exists(
    session: Session, filing_id: int, row: ParsedForm4Transaction, source_url: str
) -> bool:
    tx_hash = _transaction_hash(filing_id, row)
    if session.query(InsiderTransaction.id).filter_by(transaction_hash=tx_hash).first() is not None:
        return True
    return (
        session.query(InsiderTransaction.id)
        .filter_by(
            filing_id=filing_id,
            owner_name=row.owner_name,
            transaction_date=row.transaction_date,
            transaction_code=row.transaction_code,
            shares=row.shares,
            price=row.price,
            direct_or_indirect=row.direct_or_indirect,
            shares_owned_after=row.shares_owned_after,
            source_url=source_url,
        )
        .first()
        is not None
    )


def store_form4_transactions(
    session: Session,
    filing_id: int,
    raw_xml: str,
    source_url: str,
    fallback_ticker: str | None = None,
) -> int:
    rows = parse_form4_xml(raw_xml)
    inserted = 0
    for row in rows:
        if _transaction_exists(session, filing_id, row, source_url):
            continue
        tx_hash = _transaction_hash(filing_id, row)
        session.add(
            InsiderTransaction(
                filing_id=filing_id,
                ticker=(fallback_ticker or row.issuer_ticker or "").upper(),
                filing_ticker=(fallback_ticker or "").upper() or None,
                issuer_ticker=(row.issuer_ticker or "").upper() or None,
                issuer_name=row.issuer_name,
                reporting_owner_name=row.reporting_owner_name,
                reporting_owner_cik=row.reporting_owner_cik,
                relationship_to_issuer=row.relationship_to_issuer,
                owner_name=row.owner_name,
                owner_role=row.owner_role,
                transaction_date=row.transaction_date,
                transaction_code=row.transaction_code,
                transaction_type=row.transaction_type,
                shares=row.shares,
                price=row.price,
                direct_or_indirect=row.direct_or_indirect,
                shares_owned_after=row.shares_owned_after,
                security_title=row.security_title,
                transaction_table=row.transaction_table,
                transaction_row_index=row.transaction_row_index,
                footnote_ids=row.footnote_ids,
                ownership_form=row.ownership_form,
                deemed_execution_date=row.deemed_execution_date,
                transaction_hash=tx_hash,
                source_url=source_url,
                collected_at=utc_now_iso(),
                importance=row.importance,
            )
        )
        inserted += 1
    return inserted
