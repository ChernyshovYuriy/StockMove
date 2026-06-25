from sqlalchemy.orm import Session

from market_info_layer.analysis.form4_parser import parse_form4_xml
from market_info_layer.analysis.form8k_parser import parse_8k_items
from market_info_layer.collectors import sec_documents
from market_info_layer.collectors.sec_documents import process_sec_filings
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import Filing, FilingDocument, FilingEvent, InsiderTransaction

FORM4_XML = """<?xml version=\"1.0\"?>
<ownershipDocument>
  <issuer><issuerTradingSymbol>ABC</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Jane Insider</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship>
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


def _session(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(url)
    return Session(get_engine(url))


def _filing(form_type="4", accession="0001"):
    return Filing(
        ticker="ABC",
        cik="0000000001",
        form_type=form_type,
        filing_date="2026-06-20",
        report_date="2026-06-20",
        accession_number=accession,
        primary_document="doc.xml",
        filing_url="https://www.sec.gov/Archives/doc.xml",
        source="SEC EDGAR submissions",
        collected_at="2026-06-20T00:00:00Z",
    )


def test_form4_parser_with_mocked_xml():
    rows = parse_form4_xml(FORM4_XML)
    assert len(rows) == 1
    assert rows[0].ticker == "ABC"
    assert rows[0].owner_name == "Jane Insider"
    assert rows[0].owner_role == "Director, Officer (CFO)"
    assert rows[0].transaction_type == "Purchase"
    assert rows[0].importance == "high"


def test_8k_parser_with_mocked_html_text():
    rows = parse_8k_items(
        "<html><body>Item 5.02 Departure of Directors. CFO resigned.</body></html>"
    )
    assert rows[0]["sec_item"] == "Item 5.02"
    assert rows[0]["event_type"] == "Departure/election of directors or officers"


def test_document_downloader_does_not_redownload_existing(monkeypatch, tmp_path):
    with _session(tmp_path) as session:
        filing = _filing()
        session.add(filing)
        session.commit()
        session.add(
            FilingDocument(
                filing_id=filing.id,
                ticker="ABC",
                form_type="4",
                source_url=filing.filing_url,
                raw_text="x",
                raw_xml="x",
                downloaded_at="now",
            )
        )
        session.commit()
        monkeypatch.setattr(
            sec_documents,
            "download_filing_document",
            lambda url: (_ for _ in ()).throw(AssertionError("redownloaded")),
        )
        assert process_sec_filings(session, limit=10) == 0


def test_process_sec_filings_respects_limit_and_form_type(monkeypatch, tmp_path):
    with _session(tmp_path) as session:
        session.add(_filing("4", "0001"))
        session.add(_filing("8-K", "0002"))
        session.commit()
        monkeypatch.setattr(sec_documents, "download_filing_document", lambda url: FORM4_XML)
        assert process_sec_filings(session, limit=1, form_type="4") == 1
        assert session.query(InsiderTransaction).count() == 1
        assert session.query(Filing).filter_by(form_type="8-K", processed=False).count() == 1


def test_daily_brief_includes_parsed_filing_events(tmp_path):
    from market_info_layer.analysis.daily_brief import generate_daily_brief

    with _session(tmp_path) as session:
        session.add(
            FilingEvent(
                filing_id=1,
                ticker="ABC",
                form_type="8-K",
                event_date="2026-06-20",
                event_type="Other events",
                sec_item="Item 8.01",
                headline="Other events",
                summary="Company update",
                importance="medium",
                source_url="https://www.sec.gov/x",
                needs_human_review=False,
                created_at="now",
            )
        )
        session.commit()
        path = generate_daily_brief(
            session, output_dir=tmp_path, brief_date=__import__("datetime").date(2026, 6, 20)
        )
        assert "## Parsed filing events" in path.read_text()
        assert "ABC Item 8.01" in path.read_text()
