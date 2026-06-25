from sqlalchemy.orm import Session

from market_info_layer.collectors.sec_edgar import (
    collect_sec_filings,
    pad_cik,
    parse_recent_filings,
)
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import Filing


def test_sec_cik_padding():
    assert pad_cik("320193") == "0000320193"


def test_sec_user_agent_is_sent(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"filings": {"recent": {"form": []}}}

    def fake_get(url, headers, timeout):
        seen["url"] = url
        seen["headers"] = headers
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr("market_info_layer.collectors.sec_edgar.requests.get", fake_get)
    from market_info_layer.collectors.sec_edgar import fetch_submissions

    fetch_submissions("320193", "Test Agent test@example.com")
    assert seen["headers"]["User-Agent"] == "Test Agent test@example.com"
    assert seen["url"].endswith("CIK0000320193.json")


def test_parse_recent_filings_filters_forms():
    payload = {
        "filings": {
            "recent": {
                "form": ["8-K", "NOPE"],
                "accessionNumber": ["0000320193-24-000001", "x"],
                "filingDate": ["2024-01-02", "2024-01-03"],
                "reportDate": ["2024-01-01", "2024-01-02"],
                "primaryDocument": ["a.htm", "b.htm"],
            }
        }
    }
    rows = parse_recent_filings("AAPL", "320193", payload)
    assert len(rows) == 1
    assert rows[0]["accession_number"] == "0000320193-24-000001"


def test_duplicate_filings_are_ignored(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'sec.db'}"
    init_db(db_url)
    watch = tmp_path / "watchlist.yaml"
    watch.write_text("""
tickers:
  - ticker: AAPL
    cik: '320193'
""")
    payload = {
        "filings": {
            "recent": {
                "form": ["8-K"],
                "accessionNumber": ["0000320193-24-000001"],
                "filingDate": ["2024-01-02"],
                "reportDate": ["2024-01-01"],
                "primaryDocument": ["a.htm"],
            }
        }
    }
    monkeypatch.setattr(
        "market_info_layer.collectors.sec_edgar.fetch_submissions", lambda cik: payload
    )
    with Session(get_engine(db_url)) as session:
        assert collect_sec_filings(session, watch, delay_seconds=0) == 1
        assert collect_sec_filings(session, watch, delay_seconds=0) == 0
        assert session.query(Filing).count() == 1
