from datetime import date

from sqlalchemy.orm import Session

from market_info_layer.analysis.daily_brief import generate_daily_brief
from market_info_layer.db.database import get_engine, init_db


def test_daily_brief_creates_markdown_file(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        path = generate_daily_brief(session, date(2024, 1, 2), tmp_path / "reports")
    assert path.exists()
    assert "# Market Information Layer Daily Brief" in path.read_text()
