from sqlalchemy.orm import Session

from market_info_layer.cli import load_watchlist
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import Ticker, Watchlist


def test_watchlist_yaml_loads_into_database(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'watch.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from market_info_layer.settings import get_settings

    get_settings.cache_clear()
    init_db(db_url)
    yaml_path = tmp_path / "watchlist.yaml"
    yaml_path.write_text("""
tickers:
  - ticker: MSFT
    company_name: Microsoft Corp.
    cik: '789019'
    active: true
    reason_watching: Cloud research
    thesis: Durable software business
    confidence: 4
    status: watching
""")
    load_watchlist(yaml_path)
    with Session(get_engine(db_url)) as session:
        assert session.get(Ticker, "MSFT").cik == "789019"
        assert (
            session.query(Watchlist).filter_by(ticker="MSFT").one().reason_watching
            == "Cloud research"
        )
