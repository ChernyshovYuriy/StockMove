import pytest

from market_info_layer.collectors.nasdaq_halts import (
    HaltFetchError,
    HaltParseError,
    parse_halts_html,
    parse_halts_rss,
)

RSS_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Trade Halts</title><item>
<title>Trade Halt - ABC</title>
<pubDate>Tue, 02 Jan 2024 15:00:00 -0500</pubDate>
<description><![CDATA[
Issue Symbol: ABC | Halt Time: 2024-01-01 10:00:00 |
Resume Time: 2024-01-01 10:30:00 | Reason Code: T1 | Reason: News pending
]]></description>
</item></channel></rss>"""

RSS_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Trade Halts</title></channel></rss>"""


class _Response:
    def __init__(self, text=RSS_ONE, status_code=200, content_type="application/rss+xml"):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

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
            "halt_date": "2024-01-01",
            "halt_time": "10:00:00",
            "halt_datetime": "2024-01-01T10:00:00 America/New_York",
            "resume_time": "10:30:00",
            "resume_datetime": "2024-01-01T10:30:00 America/New_York",
            "timezone": "America/New_York",
            "reason_code": "T1",
            "reason_text": "News pending",
            "source": "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts",
            "collected_at": rows[0]["collected_at"],
        }
    ]



def test_parse_halts_rss_publication_date_fallback_and_missing_resume():
    rss = """<rss><channel><item>
    <title>Trade Halt - XYZ</title><pubDate>Tue, 02 Jan 2024 15:00:00 -0500</pubDate>
    <description>Issue Symbol: XYZ | Halt Time: 13:31:38.823 | Reason Code: LUDP</description>
    </item></channel></rss>"""

    row = parse_halts_rss(rss)[0]

    assert row["halt_date"] == "2024-01-02"
    assert row["halt_time"] == "13:31:38.823"
    assert row["halt_datetime"] == "2024-01-02T13:31:38.823 America/New_York"
    assert row["resume_time"] is None
    assert row["resume_datetime"] is None
    assert row["reason_text"] == "Limit Up-Limit Down pause"


def test_parse_halts_rss_unknown_reason_code_text():
    rss = """<rss><channel><item>
    <title>Trade Halt - ZZZ</title><pubDate>Tue, 02 Jan 2024 15:00:00 -0500</pubDate>
    <description>Issue Symbol: ZZZ | Halt Time: 13:31:38 | Reason Code: X99</description>
    </item></channel></rss>"""

    row = parse_halts_rss(rss)[0]

    assert row["reason_text"] == "Unknown halt reason code: X99"

def test_parse_halts_rss_empty_feed_returns_zero_rows():
    assert parse_halts_rss(RSS_EMPTY) == []


def test_parse_halts_rss_malformed_feed_with_entries_continues(caplog):
    malformed = """<rss><channel><item><title>Trade Halt - XYZ</title>
    <description>Issue Symbol: XYZ | Halt Time: 2024-01-01 11:00:00 | Reason Code: T2</description>
    </item></channel></rssgarbage>"""

    rows = parse_halts_rss(malformed)

    assert rows[0]["ticker"] == "XYZ"
    assert rows[0]["reason_code"] == "T2"
    assert "malformed but contains" in caplog.text


def test_parse_halts_rss_malformed_feed_without_entries_raises_clear_error():
    with pytest.raises(HaltParseError, match="Malformed Nasdaq trade halt RSS feed"):
        parse_halts_rss("<rss><channel></rss>")


def test_parse_halts_rss_skips_unparseable_entry(caplog):
    rss = """<rss><channel><item>
    <title>Nasdaq Halt Notice</title><description>Reason Code: T1</description>
    </item></channel></rss>"""

    assert parse_halts_rss(rss) == []
    assert "without parseable ticker" in caplog.text


def test_collect_halts_prevents_duplicates(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from market_info_layer.collectors.nasdaq_halts import collect_halts
    from market_info_layer.db.database import get_engine, init_db

    monkeypatch.setattr(
        "market_info_layer.collectors.nasdaq_halts.requests.get",
        lambda url, headers, timeout: _Response(),
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

    monkeypatch.setattr(
        "market_info_layer.collectors.nasdaq_halts.requests.get",
        lambda url, headers, timeout: _Response(RSS_EMPTY),
    )
    monkeypatch.setattr("market_info_layer.collectors.nasdaq_halts._can_fetch_now", lambda: True)
    db_url = f"sqlite:///{tmp_path / 'empty_halts.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        assert collect_halts(session) == 0


def test_collect_halts_uses_rss_headers(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from market_info_layer.collectors.nasdaq_halts import collect_halts
    from market_info_layer.db.database import get_engine, init_db

    captured = {}

    def fake_get(url, headers, timeout):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return _Response(RSS_EMPTY)

    monkeypatch.setattr("market_info_layer.collectors.nasdaq_halts.requests.get", fake_get)
    monkeypatch.setattr("market_info_layer.collectors.nasdaq_halts._can_fetch_now", lambda: True)
    db_url = f"sqlite:///{tmp_path / 'headers_halts.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        assert collect_halts(session) == 0

    assert captured["url"] == "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
    assert captured["headers"]["User-Agent"]
    assert captured["headers"]["Accept"] == "application/rss+xml, application/xml, text/xml, */*"


def test_collect_halts_html_response_fails_before_parse(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from market_info_layer.collectors.nasdaq_halts import collect_halts
    from market_info_layer.db.database import get_engine, init_db

    def fail_parse(text):
        raise AssertionError("HTML should not be passed to feedparser")

    monkeypatch.setattr("market_info_layer.collectors.nasdaq_halts.parse_halts_rss", fail_parse)
    monkeypatch.setattr(
        "market_info_layer.collectors.nasdaq_halts.requests.get",
        lambda url, headers, timeout: _Response(
            "<html><body>error</body></html>", content_type="text/html"
        ),
    )
    monkeypatch.setattr("market_info_layer.collectors.nasdaq_halts._can_fetch_now", lambda: True)
    db_url = f"sqlite:///{tmp_path / 'html_halts.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        with pytest.raises(HaltFetchError, match="non-RSS content"):
            collect_halts(session)


def test_collect_halts_oops_response_fails_clearly(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from market_info_layer.collectors.nasdaq_halts import collect_halts
    from market_info_layer.db.database import get_engine, init_db

    monkeypatch.setattr(
        "market_info_layer.collectors.nasdaq_halts.requests.get",
        lambda url, headers, timeout: _Response(
            "Oops! That didn't work", content_type="text/plain"
        ),
    )
    monkeypatch.setattr("market_info_layer.collectors.nasdaq_halts._can_fetch_now", lambda: True)
    db_url = f"sqlite:///{tmp_path / 'oops_halts.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        with pytest.raises(HaltFetchError, match="non-RSS content"):
            collect_halts(session)


def test_parse_halts_rss_overnight_resume_rolls_to_next_day():
    rss = """<rss><channel><item>
    <title>Trade Halt - XYZ</title><pubDate>Tue, 02 Jan 2024 23:00:00 -0500</pubDate>
    <description>Issue Symbol: XYZ | Halt Time: 23:55:00 | Resume Time: 00:10:00 |
    Reason Code: T1</description>
    </item></channel></rss>"""

    row = parse_halts_rss(rss)[0]

    assert row["halt_datetime"] == "2024-01-02T23:55:00 America/New_York"
    assert row["resume_datetime"] == "2024-01-03T00:10:00 America/New_York"


def test_parse_halts_rss_invalid_resume_time_stays_unresolved():
    rss = """<rss><channel><item>
    <title>Trade Halt - XYZ</title><pubDate>Tue, 02 Jan 2024 15:00:00 -0500</pubDate>
    <description>Issue Symbol: XYZ | Halt Time: 13:00:00 | Resume Time: TBD |
    Reason Code: T1</description>
    </item></channel></rss>"""

    row = parse_halts_rss(rss)[0]

    assert row["resume_time"] == "TBD"
    assert row["resume_datetime"] is None
