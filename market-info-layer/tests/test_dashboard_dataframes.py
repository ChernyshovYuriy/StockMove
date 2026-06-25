from sqlalchemy.orm import Session

from market_info_layer.dashboard.dataframes import dashboard_rows
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import Watchlist


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
