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


def test_compact_report_truncates_long_event_summaries(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    long_summary = " ".join(["important"] * 80)
    with Session(get_engine(db_url)) as session:
        session.add(_event(event_date="2024-04-01", summary=long_summary))
        session.commit()
        path = generate_daily_brief(session, date(2024, 4, 1), tmp_path / "reports")

    text = path.read_text()
    summary_line = next(line for line in text.splitlines() if line.startswith("Summary: "))
    assert len(summary_line.removeprefix("Summary: ")) <= 240
    parsed_section = text.split("## Parsed filing events", 1)[1].split("## Low-importance", 1)[0]
    assert "event_date=" not in parsed_section
    assert "needs_human_review=" not in parsed_section


def test_debug_report_includes_full_event_fields(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add(_event(event_date="2024-04-01", summary="debug fields"))
        session.commit()
        path = generate_daily_brief(session, date(2024, 4, 1), tmp_path / "reports", style="debug")

    text = path.read_text()
    assert "event_date=2024-04-01" in text
    assert "form_type=8-K" in text
    assert "needs_human_review=False" in text


def test_item_901_hidden_by_default_in_compact_mode(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        item = _event(event_date="2024-04-01", summary="exhibits only")
        item.sec_item = "Item 9.01"
        session.add(item)
        session.commit()
        path = generate_daily_brief(session, date(2024, 4, 1), tmp_path / "reports")

    text = path.read_text()
    assert "Item 9.01" not in text
    assert "exhibits only" not in text


def test_include_low_includes_item_901_in_compact_mode(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        item = _event(event_date="2024-04-01", importance="low", summary="exhibits included")
        item.sec_item = "Item 9.01"
        session.add(item)
        session.commit()
        path = generate_daily_brief(
            session, date(2024, 4, 1), tmp_path / "reports", include_low=True
        )

    text = path.read_text()
    assert "Item 9.01" in text
    assert "exhibits included" in text


def test_max_unprocessed_limits_unprocessed_filing_list(tmp_path):
    from market_info_layer.db.models import Filing

    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        for index in range(3):
            session.add(
                Filing(
                    ticker=f"T{index}",
                    cik=str(index),
                    form_type="8-K",
                    filing_date=f"2024-04-0{index + 1}",
                    report_date=None,
                    accession_number=f"acc-{index}",
                    primary_document=None,
                    filing_url=f"https://www.sec.gov/{index}",
                    source="sec",
                    collected_at="2024-04-01T00:00:00+00:00",
                    processed=False,
                )
            )
        session.commit()
        path = generate_daily_brief(
            session,
            date(2024, 4, 1),
            tmp_path / "reports",
            max_unprocessed=2,
        )

    text = path.read_text()
    unprocessed_section = text.split("## Unprocessed material filings", 1)[1].split(
        "## Needs human review", 1
    )[0]
    assert "T2 8-K" in unprocessed_section
    assert "T1 8-K" in unprocessed_section
    assert "T0 8-K" not in unprocessed_section
    assert "1 more unprocessed material filings not shown" in unprocessed_section


def test_daily_brief_includes_macro_context_and_trading_halts(tmp_path):
    from market_info_layer.db.models import MacroObservation, TradingHalt, Watchlist

    db_url = f"sqlite:///{tmp_path / 'brief_context.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add_all(
            [
                Watchlist(ticker="AAPL", updated_at="2024-01-01"),
                MacroObservation(
                    series_id="CPIAUCSL",
                    observation_date="2024-01-01",
                    value=300.1,
                    realtime_start=None,
                    realtime_end=None,
                    source="FRED",
                    collected_at="2024-01-02T00:00:00+00:00",
                ),
                TradingHalt(
                    ticker="AAPL",
                    halt_date="2024-01-02",
                    halt_time="10:00",
                    halt_datetime="2024-01-02T10:00 America/New_York",
                    timezone="America/New_York",
                    resume_time="10:30",
                    resume_datetime="2024-01-02T10:30 America/New_York",
                    reason_code="T1",
                    reason_text="News pending",
                    source="Nasdaq",
                    collected_at="2024-01-02T00:00:00+00:00",
                ),
            ]
        )
        session.commit()
        path = generate_daily_brief(session, date(2024, 1, 2), tmp_path / "reports")

    text = path.read_text()
    assert "## Macro Context" in text
    assert "CPIAUCSL" in text
    assert "## Trading Halts" in text
    assert "2024-01-02T10:00 America/New_York AAPL" in text


def test_daily_brief_no_watchlist_trading_halts_message(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief_no_halts.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        path = generate_daily_brief(session, date(2024, 1, 2), tmp_path / "reports")

    assert "No trading halts recorded for watchlist tickers." in path.read_text()


def test_report_mode_event_date_excludes_old_backfilled_events(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add(
            _event(
                event_date="2019-01-01",
                created_at="2026-06-26T09:00:00+00:00",
                summary="old backfill",
            )
        )
        session.commit()
        event_path = generate_daily_brief(
            session, date(2026, 6, 26), tmp_path / "reports", report_mode="event_date"
        )
        processed_path = generate_daily_brief(
            session,
            date(2026, 6, 26),
            tmp_path / "reports",
            report_mode="processed_at",
            output_name="processed",
        )
    assert "# Events by Event Date" in event_path.read_text()
    assert "old backfill" not in event_path.read_text()
    assert "# Events Processed During Backfill" in processed_path.read_text()
    assert "old backfill" in processed_path.read_text()


def test_price_context_unavailable_does_not_say_needs_human_review(tmp_path):
    from market_info_layer.db.models import Price

    db_url = f"sqlite:///{tmp_path / 'brief.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        session.add(_event(event_date="2019-01-01", importance="high", summary="old event"))
        session.add(
            Price(
                ticker="AAPL",
                price_date="2024-06-26",
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
                is_complete=True,
                source="test",
                collected_at="2026-06-26T00:00:00Z",
            )
        )
        session.commit()
        path = generate_daily_brief(session, date(2019, 1, 1), tmp_path / "reports")
    text = path.read_text()
    assert "Price context unavailable: event predates available price history." in text
    assert "Price reaction around event; same-period movement; needs human review" not in text
