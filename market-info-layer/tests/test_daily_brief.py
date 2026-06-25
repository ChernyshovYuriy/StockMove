from datetime import date

from sqlalchemy.orm import Session

from market_info_layer.analysis.daily_brief import generate_daily_brief
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import FilingEvent


def _event(
    *,
    ticker="AAPL",
    event_date="2024-01-01",
    importance="high",
    created_at="2024-04-01T12:00:00+00:00",
    source_url="https://www.sec.gov/example",
    summary="AAPL summary",
):
    return FilingEvent(
        filing_id=1,
        ticker=ticker,
        form_type="8-K",
        event_date=event_date,
        event_type="material_event",
        sec_item="Item 2.02",
        headline=f"{ticker} headline",
        summary=summary,
        importance=importance,
        source_url=source_url,
        needs_human_review=False,
        created_at=created_at,
    )


def test_daily_brief_creates_markdown_file(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        path = generate_daily_brief(session, date(2024, 1, 2), tmp_path / "reports")
    assert path.exists()
    assert "# Market Information Layer Daily Brief" in path.read_text()


def test_daily_brief_default_date_excludes_older_events(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add(_event(event_date="2024-01-01", summary="older event"))
        session.commit()
        path = generate_daily_brief(session, date(2024, 4, 1), tmp_path / "reports")

    text = path.read_text()
    assert "older event" not in text
    assert "No parsed filing events for this selection" in text


def test_daily_brief_lookback_days_includes_older_events(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add(_event(event_date="2024-01-15", summary="inside lookback"))
        session.commit()
        path = generate_daily_brief(
            session, date(2024, 4, 1), tmp_path / "reports", lookback_days=90
        )

    assert "inside lookback" in path.read_text()


def test_daily_brief_processed_today_includes_events_created_today(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add(
            _event(
                event_date="2024-01-15",
                created_at="2024-04-01T09:00:00+00:00",
                summary="created today",
            )
        )
        session.commit()
        path = generate_daily_brief(
            session, date(2024, 4, 1), tmp_path / "reports", processed_today=True
        )

    text = path.read_text()
    assert "## Recently processed filing events" in text
    assert "created today" in text


def test_low_events_are_separated_unless_include_low(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add(_event(event_date="2024-04-01", importance="low", summary="low event"))
        session.commit()
        path = generate_daily_brief(session, date(2024, 4, 1), tmp_path / "reports")
        include_path = generate_daily_brief(
            session,
            date(2024, 4, 1),
            tmp_path / "reports",
            include_low=True,
            output_name="include-low",
        )

    text = path.read_text()
    assert text.index("## Low-importance parsed filing events") < text.index("low event")
    include_text = include_path.read_text()
    assert include_text.index("## Parsed filing events") < include_text.index("low event")
    assert "No low-importance parsed filing events" in include_text


def test_high_and_medium_events_are_sorted_before_low_events(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add_all(
            [
                _event(ticker="LOW", event_date="2024-04-01", importance="low"),
                _event(ticker="MED", event_date="2024-03-31", importance="medium"),
                _event(ticker="HIGH", event_date="2024-03-30", importance="high"),
            ]
        )
        session.commit()
        path = generate_daily_brief(
            session,
            date(2024, 4, 1),
            tmp_path / "reports",
            lookback_days=5,
            include_low=True,
        )

    text = path.read_text()
    assert text.index("[high] HIGH") < text.index("[medium] MED") < text.index("[low] LOW")


def test_generated_markdown_contains_source_urls(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add(_event(event_date="2024-04-01", source_url="https://www.sec.gov/aapl-8k"))
        session.commit()
        path = generate_daily_brief(session, date(2024, 4, 1), tmp_path / "reports")

    assert "https://www.sec.gov/aapl-8k" in path.read_text()
