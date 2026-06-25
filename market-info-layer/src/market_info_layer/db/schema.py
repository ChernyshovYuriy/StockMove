from pydantic import BaseModel


class WatchlistItem(BaseModel):
    ticker: str
    company_name: str | None = None
    cik: str | None = None
    sector: str | None = None
    industry: str | None = None
    active: bool = True
    reason_watching: str | None = None
    thesis: str | None = None
    invalidation_condition: str | None = None
    catalyst: str | None = None
    next_known_date: str | None = None
    confidence: int | None = None
    status: str | None = "watching"
