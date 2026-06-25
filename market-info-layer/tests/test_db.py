from sqlalchemy import inspect

from market_info_layer.db.database import get_engine, init_db


def test_database_tables_are_created(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    tables = set(inspect(get_engine(db_url)).get_table_names())
    assert {
        "tickers",
        "watchlist",
        "filings",
        "macro_events",
        "macro_observations",
        "trading_halts",
        "prices",
        "daily_notes",
        "ai_analysis_placeholder",
    }.issubset(tables)
