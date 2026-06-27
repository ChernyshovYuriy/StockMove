# ruff: noqa: E501, E701
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from market_info_layer.settings import get_settings


def get_engine(database_url: str | None = None):
    url = database_url or get_settings().resolved_database_url()
    if url.startswith("sqlite:///"):
        db_path = Path(url.removeprefix("sqlite:///"))
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True)


def init_db(database_url: str | None = None) -> None:
    from market_info_layer.db.models import Base

    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    _ensure_trading_halt_columns(engine)
    _ensure_price_columns(engine)
    _ensure_filing_processing_status(engine)
    _ensure_insider_transaction_columns(engine)
    _ensure_filing_event_columns(engine)
    _backfill_filing_event_hashes(engine)
    _ensure_common_indexes(engine)


def _ensure_trading_halt_columns(engine) -> None:
    inspector = inspect(engine)
    if "trading_halts" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("trading_halts")}
    required = {
        "halt_date": "TEXT",
        "halt_datetime": "TEXT",
        "resume_datetime": "TEXT",
        "timezone": "TEXT",
    }
    missing = [
        (name, column_type) for name, column_type in required.items() if name not in existing
    ]
    if not missing:
        return
    with engine.begin() as conn:
        for name, column_type in missing:
            conn.execute(text(f"ALTER TABLE trading_halts ADD COLUMN {name} {column_type}"))


def get_session(database_url: str | None = None) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=get_engine(database_url), expire_on_commit=False, future=True)
    with factory() as session:
        yield session


def _ensure_price_columns(engine) -> None:
    inspector = inspect(engine)
    if "prices" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("prices")}
    if "is_complete" in existing:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE prices ADD COLUMN is_complete BOOLEAN DEFAULT 1"))
        conn.execute(text("UPDATE prices SET is_complete = 1 WHERE is_complete IS NULL"))


def _ensure_filing_processing_status(engine) -> None:
    inspector = inspect(engine)
    if "filings" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("filings")}
    with engine.begin() as conn:
        if "processing_status" not in existing:
            conn.execute(text("ALTER TABLE filings ADD COLUMN processing_status TEXT"))
        if "processing_error" not in existing:
            conn.execute(text("ALTER TABLE filings ADD COLUMN processing_error TEXT"))
        conn.execute(
            text(
                "UPDATE filings SET processing_status = "
                "CASE WHEN processed = 1 THEN 'parsed' ELSE 'discovered' END "
                "WHERE processing_status IS NULL OR processing_status IN ('pending', 'processed', '')"
            )
        )


def _ensure_common_indexes(engine) -> None:
    inspector = inspect(engine)
    table_indexes = {
        "filings": [
            ("ix_filings_ticker_filing_date", "ticker, filing_date"),
            ("ix_filings_accession_number", "accession_number"),
        ],
        "filing_events": [
            ("ix_filing_events_filing_date_type", "filing_id, event_date, event_type"),
        ],
        "insider_transactions": [
            ("ix_insider_ticker_transaction_date", "ticker, transaction_date"),
            ("ix_insider_owner_name", "owner_name"),
        ],
        "trading_halts": [
            ("ix_trading_halts_ticker_halt_date", "ticker, halt_date"),
        ],
    }
    with engine.begin() as conn:
        for table, indexes in table_indexes.items():
            if table not in inspector.get_table_names():
                continue
            existing = {idx["name"] for idx in inspector.get_indexes(table)}
            for name, columns in indexes:
                if name not in existing:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))


def _ensure_insider_transaction_columns(engine) -> None:
    inspector = inspect(engine)
    if "insider_transactions" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("insider_transactions")}
    required = {
        "filing_ticker": "TEXT",
        "issuer_ticker": "TEXT",
        "issuer_name": "TEXT",
        "reporting_owner_name": "TEXT",
        "reporting_owner_cik": "TEXT",
        "relationship_to_issuer": "TEXT",
        "security_title": "TEXT",
        "transaction_table": "TEXT",
        "transaction_row_index": "INTEGER",
        "footnote_ids": "TEXT",
        "ownership_form": "TEXT",
        "deemed_execution_date": "TEXT",
        "transaction_hash": "TEXT",
    }
    with engine.begin() as conn:
        for name, column_type in required.items():
            if name not in existing:
                conn.execute(
                    text(f"ALTER TABLE insider_transactions ADD COLUMN {name} {column_type}")
                )
        conn.execute(
            text("UPDATE insider_transactions SET filing_ticker = COALESCE(filing_ticker, ticker)")
        )
        conn.execute(
            text("UPDATE insider_transactions SET issuer_ticker = COALESCE(issuer_ticker, ticker)")
        )
        conn.execute(
            text(
                "UPDATE insider_transactions SET reporting_owner_name = "
                "COALESCE(reporting_owner_name, owner_name)"
            )
        )
        conn.execute(
            text(
                "UPDATE insider_transactions SET relationship_to_issuer = "
                "COALESCE(relationship_to_issuer, owner_role)"
            )
        )


def _ensure_filing_event_columns(engine) -> None:
    inspector = inspect(engine)
    if "filing_events" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("filing_events")}
    with engine.begin() as conn:
        if "event_hash" not in existing:
            conn.execute(text("ALTER TABLE filing_events ADD COLUMN event_hash TEXT"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_filing_events_event_hash ON filing_events(event_hash) WHERE event_hash IS NOT NULL"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_insider_transaction_hash ON insider_transactions(transaction_hash) WHERE transaction_hash IS NOT NULL"))


def _backfill_filing_event_hashes(engine) -> None:
    inspector = inspect(engine)
    if "filing_events" not in inspector.get_table_names():
        return
    from market_info_layer.analysis.event_hash import deterministic_event_hash

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, filing_id, sec_item, event_type, event_date, headline, summary FROM filing_events WHERE event_hash IS NULL OR event_hash = ''")).mappings().all()
        for row in rows:
            event_hash = deterministic_event_hash(
                filing_id=row["filing_id"],
                sec_item=row["sec_item"],
                event_type=row["event_type"],
                event_date=row["event_date"],
                headline=row["headline"],
                summary=row["summary"],
            )
            conn.execute(text("UPDATE filing_events SET event_hash = :event_hash WHERE id = :id"), {"event_hash": event_hash, "id": row["id"]})
