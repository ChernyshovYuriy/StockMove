from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_info_layer.analysis.daily_brief import generate_daily_brief
from market_info_layer.analysis.price_context import event_price_reaction
from market_info_layer.collectors import prices as price_collector
from market_info_layer.collectors.prices import PriceBar, PriceProvider, collect_prices
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import FilingEvent, Price, Watchlist


class MockPriceProvider(PriceProvider):
    def daily_prices(self, ticker, *, period="2y", start=None, end=None):
        return [
            PriceBar(ticker, "2024-01-02", 10, 11, 9, 10, 100, "mock"),
            PriceBar(ticker, "2024-01-03", 11, 12, 10, 11, 200, "mock"),
        ]


class CurrentDayProvider(PriceProvider):
    def daily_prices(self, ticker, *, period="2y", start=None, end=None):
        return [
            PriceBar(ticker, "2026-06-24", 10, 11, 9, 10, 100, "mock"),
            PriceBar(ticker, "2026-06-25", 11, 12, 10, 11, 200, "mock"),
        ]


def _session(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'prices.db'}"
    init_db(db_url)
    return Session(get_engine(db_url))


def _event(importance="high", sec_item="Item 2.02"):
    return FilingEvent(
        filing_id=1,
        ticker="AAPL",
        form_type="8-K",
        event_date="2024-01-22",
        event_type="material_event",
        sec_item=sec_item,
        headline="headline",
        summary="summary",
        importance=importance,
        source_url="https://www.sec.gov/example",
        needs_human_review=False,
        created_at="2024-01-22T12:00:00+00:00",
    )


def _add_price(session, day, close, *, complete=True, volume=1000):
    session.add(
        Price(
            ticker="AAPL",
            price_date=day.isoformat(),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            is_complete=complete,
            source="mock",
            collected_at="now",
        )
    )


def test_collect_prices_uses_mock_provider_and_prevents_duplicates(tmp_path):
    with _session(tmp_path) as session:
        session.add(Watchlist(ticker="AAPL", status="watching", updated_at="now"))
        session.commit()
        assert collect_prices(session, provider=MockPriceProvider()) == 2
        assert collect_prices(session, provider=MockPriceProvider()) == 0
        assert len(session.scalars(select(Price)).all()) == 2


def test_collect_prices_excludes_current_day_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(price_collector, "_us_trading_today", lambda: date(2026, 6, 25))
    with _session(tmp_path) as session:
        assert collect_prices(session, provider=CurrentDayProvider(), ticker="AAPL") == 1
        rows = session.scalars(select(Price).order_by(Price.price_date)).all()
        assert [row.price_date for row in rows] == ["2026-06-24"]
        assert rows[0].is_complete is True


def test_collect_prices_include_current_day_marks_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(price_collector, "_us_trading_today", lambda: date(2026, 6, 25))
    with _session(tmp_path) as session:
        assert (
            collect_prices(
                session,
                provider=CurrentDayProvider(),
                ticker="AAPL",
                include_current_day=True,
            )
            == 2
        )
        rows = session.scalars(select(Price).order_by(Price.price_date)).all()
        assert rows[0].is_complete is True
        assert rows[1].price_date == "2026-06-25"
        assert rows[1].is_complete is False


def test_event_price_reaction_returns_window_context(tmp_path):
    with _session(tmp_path) as session:
        start = date(2023, 12, 20)
        for i in range(30):
            day = start + timedelta(days=i)
            _add_price(session, day, 100 + i, volume=1000 + i)
        session.commit()
        reaction = event_price_reaction(session, "AAPL", "2024-01-10")
        assert reaction.status == "ok"
        assert reaction.close_prev == 120
        assert reaction.close_event_or_next == 121
        assert reaction.close_plus_1 == 122
        assert reaction.close_plus_5 == 126
        assert round(reaction.pct_1d, 4) == round((122 - 121) / 121 * 100, 4)
        assert reaction.volume_event == 1021
        assert reaction.avg_volume_20d is not None
        assert reaction.volume_ratio is not None


def test_event_price_reaction_ignores_incomplete_current_day_rows_by_default(tmp_path):
    with _session(tmp_path) as session:
        _add_price(session, date(2026, 6, 24), 100, complete=True)
        _add_price(session, date(2026, 6, 25), 90, complete=False)
        session.commit()
        reaction = event_price_reaction(session, "AAPL", "2026-06-25")
        assert reaction.close_event_or_next is None
        assert reaction.status == "incomplete_price_window"


def test_report_compact_shows_concise_price_context_for_high_medium_events(tmp_path):
    with _session(tmp_path) as session:
        session.add(_event("medium"))
        for offset, close in enumerate([100, 101, 103, 104, 105, 106, 107, 108, 109]):
            _add_price(session, date(2024, 1, 19) + timedelta(days=offset), close)
        session.commit()
        path = generate_daily_brief(session, date(2024, 1, 22), tmp_path / "reports")
        text = path.read_text()
        assert "Price context: +1d" in text
        assert "same-period movement" in text
        assert "close_prev=" not in text


def test_report_compact_does_not_show_price_context_for_item_901_by_default(tmp_path):
    with _session(tmp_path) as session:
        session.add(_event("high", "Item 9.01"))
        for offset, close in enumerate([100, 101, 103, 104, 105, 106, 107, 108, 109]):
            _add_price(session, date(2024, 1, 19) + timedelta(days=offset), close)
        session.commit()
        path = generate_daily_brief(session, date(2024, 1, 22), tmp_path / "reports")
        assert "Price context:" not in path.read_text()


def test_report_works_when_prices_are_missing(tmp_path):
    with _session(tmp_path) as session:
        session.add(_event())
        session.commit()
        path = generate_daily_brief(session, date(2024, 1, 22), tmp_path / "reports")
        assert "missing_price_data" in path.read_text()
