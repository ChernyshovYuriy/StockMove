from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
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

    Base.metadata.create_all(get_engine(database_url))


def get_session(database_url: str | None = None) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=get_engine(database_url), expire_on_commit=False, future=True)
    with factory() as session:
        yield session
