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
    if "processing_status" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE filings ADD COLUMN processing_status TEXT"))
            conn.execute(
                text(
                    "UPDATE filings SET processing_status = "
                    "CASE WHEN processed = 1 THEN 'processed' ELSE 'pending' END "
                    "WHERE processing_status IS NULL"
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
