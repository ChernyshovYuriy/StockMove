from sqlalchemy.orm import Session

from market_info_layer.db.models import Watchlist


def watchlist_items(session: Session) -> list[Watchlist]:
    return session.query(Watchlist).all()
