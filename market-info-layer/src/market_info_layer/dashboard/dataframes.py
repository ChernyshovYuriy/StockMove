from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase, Session


def dashboard_rows(session: Session, model: type[DeclarativeBase]) -> list[dict]:
    """Return dashboard-safe rows for an ORM model.

    Selecting mapped table columns and reading ``mappings()`` keeps SQLAlchemy ORM
    instance internals, such as ``_sa_instance_state``, out of Streamlit dataframes.
    """

    result = session.execute(select(*model.__table__.columns)).mappings().all()
    return [dict(row) for row in result]
