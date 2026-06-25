from sqlalchemy import select, text
from sqlalchemy.orm import DeclarativeBase, Session


def dashboard_rows(session: Session, model: type[DeclarativeBase]) -> list[dict]:
    """Return dashboard-safe rows for an ORM model.

    Selecting mapped table columns and reading ``mappings()`` keeps SQLAlchemy ORM
    instance internals, such as ``_sa_instance_state``, out of Streamlit dataframes.
    """

    result = session.execute(select(*model.__table__.columns)).mappings().all()
    return [dict(row) for row in result]


def price_summary_rows(session: Session) -> list[dict]:
    rows = session.execute(
        text("""
        SELECT
            p.ticker,
            MAX(CASE WHEN p.is_complete = 1 THEN p.price_date END) AS latest_complete_price_date,
            (
                SELECT p2.close FROM prices p2
                WHERE p2.ticker = p.ticker AND p2.is_complete = 1
                ORDER BY p2.price_date DESC, p2.source ASC LIMIT 1
            ) AS latest_close,
            (
                SELECT p2.volume FROM prices p2
                WHERE p2.ticker = p.ticker AND p2.is_complete = 1
                ORDER BY p2.price_date DESC, p2.source ASC LIMIT 1
            ) AS latest_volume,
            SUM(CASE WHEN p.is_complete = 0 THEN 1 ELSE 0 END) AS incomplete_rows
        FROM prices p
        GROUP BY p.ticker
        ORDER BY p.ticker
        """)
    ).mappings().all()
    return [dict(row) for row in rows]
