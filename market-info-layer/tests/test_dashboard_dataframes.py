from sqlalchemy.orm import Session

from market_info_layer.dashboard.dataframes import dashboard_rows, price_summary_rows
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import Price, Watchlist


def test_dashboard_rows_excludes_sqlalchemy_instance_state(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)

    with Session(get_engine(db_url)) as session:
        session.add(
            Watchlist(
                ticker="ABC",
                reason_watching="Catalyst pending",
                thesis="Example thesis",
                invalidation_condition="Example invalidation",
                catalyst="Example catalyst",
                next_known_date="2026-01-01",
                confidence=3,
                status="active",
                updated_at="2026-01-01T00:00:00Z",
            )
        )
        session.commit()

        rows = dashboard_rows(session, Watchlist)

    assert rows == [
        {
            "id": 1,
            "ticker": "ABC",
            "reason_watching": "Catalyst pending",
            "thesis": "Example thesis",
            "invalidation_condition": "Example invalidation",
            "catalyst": "Example catalyst",
            "next_known_date": "2026-01-01",
            "confidence": 3,
            "status": "active",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ]
    assert "_sa_instance_state" not in rows[0]



def test_price_summary_rows_uses_latest_complete_price_and_counts_incomplete(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'prices.db'}"
    init_db(db_url)

    with Session(get_engine(db_url)) as session:
        session.add(
            Price(
                ticker="ABC",
                price_date="2026-06-24",
                open=1,
                high=1,
                low=1,
                close=10,
                volume=100,
                is_complete=True,
                source="mock",
                collected_at="now",
            )
        )
        session.add(
            Price(
                ticker="ABC",
                price_date="2026-06-25",
                open=1,
                high=1,
                low=1,
                close=11,
                volume=50,
                is_complete=False,
                source="mock",
                collected_at="now",
            )
        )
        session.commit()

        rows = price_summary_rows(session)

    assert rows == [
        {
            "ticker": "ABC",
            "latest_complete_price_date": "2026-06-24",
            "latest_close": 10.0,
            "latest_volume": 100,
            "incomplete_rows": 1,
        }
    ]
