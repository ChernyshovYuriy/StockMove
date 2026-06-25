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
