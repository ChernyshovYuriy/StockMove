import pytest

from market_info_layer.collectors.nasdaq_halts import (
    HaltParseError,
    parse_halts_html,
    parse_halts_rss,
)

RSS_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Trade Halts</title><item>
<title>Trade Halt - ABC</title>
<description><![CDATA[
Issue Symbol: ABC | Halt Time: 2024-01-01 10:00:00 |
Resume Time: 2024-01-01 10:30:00 | Reason Code: T1 | Reason: News pending
]]></description>
</item></channel></rss>"""

RSS_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Trade Halts</title></channel></rss>"""


class _Response:
    text = RSS_ONE

    def raise_for_status(self):
        return None


def test_parse_halts_mocked_html():
    html = """
    <table>
      <tr>
        <th>Issue Symbol</th><th>Halt Time</th><th>Resumption Trade Time</th>
        <th>Reason Code</th><th>Reason</th>
      </tr>
      <tr><td>ABC</td><td>10:00</td><td>10:30</td><td>T1</td><td>News pending</td></tr>
    </table>
    """
    rows = parse_halts_html(html)
    assert rows[0]["ticker"] == "ABC"
    assert rows[0]["reason_code"] == "T1"


def test_parse_halts_clear_error_on_changed_format():
    with pytest.raises(HaltParseError):
        parse_halts_html("<html><body>No table</body></html>")


def test_parse_halts_rss_with_one_halt_entry():
    rows = parse_halts_rss(RSS_ONE)

    assert rows == [
        {
            "ticker": "ABC",
            "halt_time": "2024-01-01 10:00:00",
            "resume_time": "2024-01-01 10:30:00",
            "reason_code": "T1",
            "reason_text": "News pending",
            "source": "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts",
            "collected_at": rows[0]["collected_at"],
        }
    ]


def test_parse_halts_rss_empty_feed_returns_zero_rows():
    assert parse_halts_rss(RSS_EMPTY) == []


def test_parse_halts_rss_malformed_feed_raises_clear_error():
    with pytest.raises(HaltParseError, match="Malformed Nasdaq trade halt RSS feed"):
        parse_halts_rss("<rss><channel><item></channel></rss>")


def test_collect_halts_prevents_duplicates(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from market_info_layer.collectors.nasdaq_halts import collect_halts
    from market_info_layer.db.database import get_engine, init_db

    monkeypatch.setattr(
        "market_info_layer.collectors.nasdaq_halts.requests.get",
        lambda url, timeout: _Response(),
    )
    monkeypatch.setattr("market_info_layer.collectors.nasdaq_halts._can_fetch_now", lambda: True)
    db_url = f"sqlite:///{tmp_path / 'halts.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        assert collect_halts(session) == 1
        assert collect_halts(session) == 0


def test_collect_halts_empty_rss_succeeds_with_zero_rows(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from market_info_layer.collectors.nasdaq_halts import collect_halts
    from market_info_layer.db.database import get_engine, init_db

    class EmptyResponse:
        text = RSS_EMPTY

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "market_info_layer.collectors.nasdaq_halts.requests.get",
        lambda url, timeout: EmptyResponse(),
    )
    monkeypatch.setattr("market_info_layer.collectors.nasdaq_halts._can_fetch_now", lambda: True)
    db_url = f"sqlite:///{tmp_path / 'empty_halts.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        assert collect_halts(session) == 0
