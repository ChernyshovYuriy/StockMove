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



def test_sec_routine_collects_and_processes_8k_and_form4(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from market_info_layer import cli
    from market_info_layer.collectors import sec_documents
    from market_info_layer.db.models import FilingEvent, InsiderTransaction

    form4_xml = """<?xml version=\"1.0\"?>
<ownershipDocument>
  <issuer><issuerTradingSymbol>ABC</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Jane Insider</rptOwnerName></reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable><nonDerivativeTransaction>
    <transactionDate><value>2026-06-20</value></transactionDate>
    <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>1500</value></transactionShares><transactionPricePerShare><value>10.50</value></transactionPricePerShare></transactionAmounts>
    <postTransactionAmounts><sharesOwnedFollowingTransaction><value>2500</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
  </nonDerivativeTransaction></nonDerivativeTable>
</ownershipDocument>
"""

    db_url = f"sqlite:///{tmp_path / 'sec-routine.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setattr(cli, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(cli, "create_db", lambda: init_db(db_url))
    monkeypatch.setattr(cli, "get_engine", lambda: get_engine(db_url))
    monkeypatch.setattr(
        cli,
        "collect_sec_filings",
        lambda session: session.add_all(
            [
                Filing(
                    ticker="ABC",
                    cik="0000000001",
                    form_type="8-K",
                    filing_date="2026-06-20",
                    report_date="2026-06-20",
                    accession_number="8k-1",
                    primary_document="doc.htm",
                    filing_url="https://www.sec.gov/Archives/doc.htm",
                    source="SEC EDGAR submissions",
                    collected_at="2026-06-20T00:00:00Z",
                ),
                Filing(
                    ticker="ABC",
                    cik="0000000001",
                    form_type="4",
                    filing_date="2026-06-20",
                    report_date="2026-06-20",
                    accession_number="form4-1",
                    primary_document="doc.xml",
                    filing_url="https://www.sec.gov/Archives/doc.xml",
                    source="SEC EDGAR submissions",
                    collected_at="2026-06-20T00:00:00Z",
                ),
            ]
        )
        or session.commit()
        or 2,
    )

    def fake_download(url):
        if url.endswith("doc.xml"):
            return form4_xml
        return "Item 5.02 Departure of Directors or Certain Officers. The CFO resigned."

    monkeypatch.setattr(sec_documents, "download_filing_document", fake_download)
    result = CliRunner().invoke(cli.app, ["sec-routine", "--limit-per-form", "10"])

    assert result.exit_code == 0
    assert "processed 1 8-K filings" in result.output
    assert "processed 1 Form 4 filings" in result.output
    with Session(get_engine(db_url)) as session:
        assert session.query(FilingEvent).count() == 1
        assert session.query(InsiderTransaction).count() == 1
        assert session.query(Filing).filter_by(processed=True).count() == 2


def test_form4_xsl_primary_document_is_rewritten_to_raw_xml():
    from market_info_layer.collectors.sec_edgar import parse_recent_filings

    payload = {"filings": {"recent": {
        "form": ["4"],
        "accessionNumber": ["0000000001-26-000001"],
        "filingDate": ["2026-06-20"],
        "reportDate": ["2026-06-20"],
        "primaryDocument": ["xslF345X06/ownership.xml"],
    }}}

    row = parse_recent_filings("ABC", "1", payload)[0]

    assert row["primary_document"] == "ownership.xml"
    assert "xslF345X06" not in row["filing_url"]
    assert row["filing_url"].endswith("/ownership.xml")


def test_collect_sec_filings_attempts_every_watchlist_ticker(tmp_path, monkeypatch, caplog):
    db_url = f"sqlite:///{tmp_path / 'all.db'}"
    init_db(db_url)
    watch = tmp_path / "watchlist.yaml"
    watch.write_text("""
tickers:
  - ticker: AAPL
    cik: '320193'
  - ticker: CEG
    cik: '1868275'
  - ticker: LULU
    cik: '1397187'
  - ticker: NOCIK
n""".replace("\nn", "\n"))
    seen = []

    def fake_fetch(cik):
        seen.append(cik)
        return {"filings": {"recent": {"form": []}}}

    monkeypatch.setattr("market_info_layer.collectors.sec_edgar.fetch_submissions", fake_fetch)
    with Session(get_engine(db_url)) as session:
        assert collect_sec_filings(session, watch, delay_seconds=0) == 0

    assert seen == ["320193", "1868275", "1397187"]
    assert "no CIK mapping" in caplog.text
