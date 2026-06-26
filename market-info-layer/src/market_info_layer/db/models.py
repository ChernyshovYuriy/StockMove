from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Ticker(Base):
    __tablename__ = "tickers"
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    company_name: Mapped[str | None] = mapped_column(Text)
    cik: Mapped[str | None] = mapped_column(String)
    sector: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Watchlist(Base):
    __tablename__ = "watchlist"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String)
    reason_watching: Mapped[str | None] = mapped_column(Text)
    thesis: Mapped[str | None] = mapped_column(Text)
    invalidation_condition: Mapped[str | None] = mapped_column(Text)
    catalyst: Mapped[str | None] = mapped_column(Text)
    next_known_date: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint("ticker", name="uq_watchlist_ticker"),)


class Filing(Base):
    __tablename__ = "filings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String)
    cik: Mapped[str] = mapped_column(String)
    form_type: Mapped[str] = mapped_column(String)
    filing_date: Mapped[str | None] = mapped_column(String)
    report_date: Mapped[str | None] = mapped_column(String)
    accession_number: Mapped[str] = mapped_column(String, unique=True)
    primary_document: Mapped[str | None] = mapped_column(String)
    filing_url: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String)
    collected_at: Mapped[str] = mapped_column(String)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_status: Mapped[str | None] = mapped_column(String)
    __table_args__ = (
        Index("ix_filings_ticker_filing_date", "ticker", "filing_date"),
        Index("ix_filings_accession_number", "accession_number"),
    )


class FilingDocument(Base):
    __tablename__ = "filing_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filing_id: Mapped[int] = mapped_column(Integer, ForeignKey("filings.id"), unique=True)
    ticker: Mapped[str | None] = mapped_column(String)
    form_type: Mapped[str | None] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_xml: Mapped[str | None] = mapped_column(Text)
    raw_html: Mapped[str | None] = mapped_column(Text)
    downloaded_at: Mapped[str] = mapped_column(String)
    http_status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String)


class InsiderTransaction(Base):
    __tablename__ = "insider_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filing_id: Mapped[int] = mapped_column(Integer, ForeignKey("filings.id"))
    ticker: Mapped[str] = mapped_column(String)
    filing_ticker: Mapped[str | None] = mapped_column(String)
    issuer_ticker: Mapped[str | None] = mapped_column(String)
    issuer_name: Mapped[str | None] = mapped_column(Text)
    reporting_owner_name: Mapped[str | None] = mapped_column(Text)
    reporting_owner_cik: Mapped[str | None] = mapped_column(String)
    relationship_to_issuer: Mapped[str | None] = mapped_column(Text)
    owner_name: Mapped[str | None] = mapped_column(Text)
    owner_role: Mapped[str | None] = mapped_column(Text)
    transaction_date: Mapped[str | None] = mapped_column(String)
    transaction_code: Mapped[str | None] = mapped_column(String)
    transaction_type: Mapped[str | None] = mapped_column(String)
    shares: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    direct_or_indirect: Mapped[str | None] = mapped_column(String)
    shares_owned_after: Mapped[float | None] = mapped_column(Float)
    security_title: Mapped[str | None] = mapped_column(Text)
    transaction_table: Mapped[str | None] = mapped_column(String)
    transaction_row_index: Mapped[int | None] = mapped_column(Integer)
    footnote_ids: Mapped[str | None] = mapped_column(Text)
    ownership_form: Mapped[str | None] = mapped_column(String)
    deemed_execution_date: Mapped[str | None] = mapped_column(String)
    transaction_hash: Mapped[str | None] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[str] = mapped_column(String)
    importance: Mapped[str | None] = mapped_column(String)
    __table_args__ = (
        Index("ix_insider_ticker_transaction_date", "ticker", "transaction_date"),
        Index("ix_insider_owner_name", "owner_name"),
    )


class FilingEvent(Base):
    __tablename__ = "filing_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filing_id: Mapped[int] = mapped_column(Integer, ForeignKey("filings.id"))
    ticker: Mapped[str] = mapped_column(String)
    form_type: Mapped[str] = mapped_column(String)
    event_date: Mapped[str | None] = mapped_column(String)
    event_type: Mapped[str | None] = mapped_column(String)
    sec_item: Mapped[str | None] = mapped_column(String)
    headline: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[str | None] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(Text)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    event_hash: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    __table_args__ = (
        Index("ix_filing_events_filing_date_type", "filing_id", "event_date", "event_type"),
    )


class MacroEvent(Base):
    __tablename__ = "macro_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_date: Mapped[str | None] = mapped_column(String)
    event_name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String)
    importance: Mapped[str | None] = mapped_column(String)
    collected_at: Mapped[str] = mapped_column(String)


class MacroObservation(Base):
    __tablename__ = "macro_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[str] = mapped_column(String)
    observation_date: Mapped[str] = mapped_column(String)
    value: Mapped[float | None] = mapped_column(Float)
    realtime_start: Mapped[str | None] = mapped_column(String)
    realtime_end: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    collected_at: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint("series_id", "observation_date", name="uq_macro_obs"),)


class TradingHalt(Base):
    __tablename__ = "trading_halts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String)
    halt_date: Mapped[str | None] = mapped_column(String)
    halt_time: Mapped[str | None] = mapped_column(String)
    halt_datetime: Mapped[str | None] = mapped_column(String)
    resume_time: Mapped[str | None] = mapped_column(String)
    resume_datetime: Mapped[str | None] = mapped_column(String)
    timezone: Mapped[str | None] = mapped_column(String)
    reason_code: Mapped[str | None] = mapped_column(String)
    reason_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String)
    collected_at: Mapped[str] = mapped_column(String)
    __table_args__ = (
        UniqueConstraint("ticker", "halt_datetime", "reason_code", name="uq_trading_halt"),
        Index("ix_trading_halts_ticker_halt_date", "ticker", "halt_date"),
    )


class Price(Base):
    __tablename__ = "prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String)
    price_date: Mapped[str] = mapped_column(String)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String)
    collected_at: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint("ticker", "price_date", "source", name="uq_price"),)


class DailyNote(Base):
    __tablename__ = "daily_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_date: Mapped[str] = mapped_column(String)
    ticker: Mapped[str | None] = mapped_column(String)
    observed_move: Mapped[str | None] = mapped_column(Text)
    suspected_reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int | None] = mapped_column(Integer)
    lesson: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)


class AIAnalysisPlaceholder(Base):
    __tablename__ = "ai_analysis_placeholder"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_table: Mapped[str] = mapped_column(String)
    source_id: Mapped[int] = mapped_column(Integer)
    ticker: Mapped[str | None] = mapped_column(String)
    analysis_type: Mapped[str] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String)
