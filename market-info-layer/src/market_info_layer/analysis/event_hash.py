from __future__ import annotations

import hashlib
import re
from typing import Any


def normalize_event_hash_part(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def deterministic_event_hash(
    *,
    filing_id: int | str,
    sec_item: str | None = None,
    event_type: str | None = None,
    event_date: str | None = None,
    headline: str | None = None,
    summary: str | None = None,
    **extra: Any,
) -> str:
    parts = [filing_id, sec_item, event_type, event_date, headline, summary]
    for key in sorted(extra):
        parts.append(key)
        parts.append(extra[key])
    payload = "\x1f".join(normalize_event_hash_part(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
