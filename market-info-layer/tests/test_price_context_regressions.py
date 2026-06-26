from datetime import date

from sqlalchemy.orm import Session

from market_info_layer.analysis.price_context import event_price_reaction
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import Price


def _session(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'price.db'}"
    init_db(db_url)
    return Session(get_engine(db_url))


def _price(ticker, d, close=100.0, volume=1000):
    return Price(
        ticker=ticker,
        price_date=d,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        is_complete=True,
        source="test",
        collected_at="2026-06-26T00:00:00Z",
    )


def test_event_before_price_history_gets_no_fake_returns(tmp_path):
    with _session(tmp_path) as session:
        session.add_all([_price("AAPL", "2024-06-26"), _price("AAPL", "2024-06-27", 101)])
        session.commit()
        reaction = event_price_reaction(session, "AAPL", "2019-01-01")
    assert reaction.status == "event_predates_price_history"
    assert reaction.close_event_or_next is None
    assert reaction.pct_1d is None


def test_event_with_nearby_price_data_uses_context(tmp_path):
    with _session(tmp_path) as session:
        for idx, d in enumerate(
            ["2024-06-26", "2024-06-27", "2024-06-28", "2024-07-01", "2024-07-02", "2024-07-03"]
        ):
            session.add(_price("AAPL", d, 100 + idx))
        session.commit()
        reaction = event_price_reaction(session, "AAPL", "2024-06-26")
    assert reaction.status == "ok"
    assert reaction.baseline_date == "2024-06-26"
    assert reaction.pct_1d is not None


def test_long_gap_before_first_price_is_unavailable(tmp_path):
    with _session(tmp_path) as session:
        session.add_all([_price("PLTR", "2024-06-26"), _price("PLTR", "2024-06-27", 101)])
        session.commit()
        reaction = event_price_reaction(session, "PLTR", date(2024, 6, 1), max_baseline_gap_days=5)
    assert reaction.status == "event_predates_price_history"
    assert reaction.baseline_gap_days == 25


def test_weekend_event_can_use_next_trading_day_within_threshold(tmp_path):
    with _session(tmp_path) as session:
        for idx, d in enumerate(
            [
                "2024-06-28",
                "2024-07-01",
                "2024-07-02",
                "2024-07-03",
                "2024-07-05",
                "2024-07-08",
                "2024-07-09",
            ]
        ):
            session.add(_price("AAPL", d, 100 + idx))
        session.commit()
        reaction = event_price_reaction(session, "AAPL", "2024-06-29", max_baseline_gap_days=5)
    assert reaction.status == "ok"
    assert reaction.baseline_date == "2024-07-01"
    assert reaction.baseline_gap_days == 2
