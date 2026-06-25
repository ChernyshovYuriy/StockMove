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
    assert rows[0]["importance"] == "high"


def test_8k_ceo_resignation_is_high():
    rows = parse_8k_items("Item 5.02 CEO resignation effective immediately.")
    assert rows[0]["importance"] == "high"
    assert "Leadership change" in rows[0]["summary"]


def test_8k_routine_director_election_is_not_high():
    rows = parse_8k_items(
        "Item 5.02 At the annual meeting, shareholders approved a routine director election "
        "and committee membership update."
    )
    assert rows[0]["importance"] == "low"


def test_8k_item_901_is_low_supporting_exhibits():
    rows = parse_8k_items(
        "Item 2.02 Results of Operations and Financial Condition. The company reported "
        "quarterly results. Item 9.01 Financial Statements and Exhibits. Exhibit 99.1 is "
        "furnished herewith."
    )
    item_901 = [row for row in rows if row["sec_item"] == "Item 9.01"][0]
    assert item_901["importance"] == "low"
    assert "Supporting exhibit information" in item_901["summary"]


def test_8k_item_202_with_guidance_is_high():
    rows = parse_8k_items(
        "Item 2.02 Results of Operations and Financial Condition. The company updated "
        "full-year guidance and outlook."
    )
    assert rows[0]["importance"] == "high"


def test_8k_item_507_annual_meeting_vote_is_low():
    rows = parse_8k_items(
        "Item 5.07 Submission of Matters to a Vote of Security Holders. At the annual "
        "meeting, shareholders voted on director elections and ratified auditors."
    )
    assert rows[0]["importance"] == "low"


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
        assert "[medium] ABC — 2026-06-20 — Item 8.01" in path.read_text()


def test_form4_parser_rejects_malformed_xml():
    from market_info_layer.analysis.form4_parser import Form4ParseError

    try:
        parse_form4_xml("<ownershipDocument><issuer></ownershipDocument>")
    except Form4ParseError as exc:
        assert "malformed Form 4 XML" in str(exc)
    else:
        raise AssertionError("malformed XML should raise Form4ParseError")


def test_html_form4_response_does_not_crash_and_creates_review_event(monkeypatch, tmp_path):
    with _session(tmp_path) as session:
        filing = _filing("4", "0003")
        session.add(filing)
        session.commit()
        monkeypatch.setattr(
            sec_documents,
            "download_filing_document",
            lambda url: sec_documents.DownloadedFilingDocument(
                "<html><body>SEC transformed document</body></html>",
                url,
                "text/html",
                200,
            ),
        )

        assert process_sec_filings(session, limit=10, form_type="4") == 1
        event = session.query(FilingEvent).one()
        assert event.event_type == "Unparseable Form 4"
        assert event.needs_human_review is True
        assert event.importance == "unknown"
        assert "not raw Form 4 ownership XML" in event.summary
        assert session.get(Filing, filing.id).processed is True


def test_sec_rate_limit_form4_response_does_not_crash(monkeypatch, tmp_path):
    with _session(tmp_path) as session:
        filing = _filing("4", "0004")
        session.add(filing)
        session.commit()
        monkeypatch.setattr(
            sec_documents,
            "download_filing_document",
            lambda url: sec_documents.DownloadedFilingDocument(
                "Request Rate Threshold Exceeded", url, "text/plain", 429
            ),
        )

        assert process_sec_filings(session, limit=10, form_type="4") == 1
        event = session.query(FilingEvent).one()
        assert event.needs_human_review is True
        assert "HTTP status 429" in event.summary
        doc = session.query(FilingDocument).one()
        assert doc.raw_text == "Request Rate Threshold Exceeded"
        assert doc.http_status_code == 429


def test_process_sec_filings_continues_after_bad_form4(monkeypatch, tmp_path):
    with _session(tmp_path) as session:
        bad = _filing("4", "0005")
        bad.filing_url = "https://www.sec.gov/Archives/bad.xml"
        good = _filing("4", "0006")
        good.filing_url = "https://www.sec.gov/Archives/good.xml"
        session.add_all([bad, good])
        session.commit()

        def fake_download(url):
            if url.endswith("bad.xml"):
                return "<ownershipDocument><issuer></ownershipDocument>"
            return FORM4_XML

        monkeypatch.setattr(sec_documents, "download_filing_document", fake_download)
        assert process_sec_filings(session, limit=10, form_type="4") == 2
        assert session.query(FilingEvent).filter_by(needs_human_review=True).count() == 1
        assert session.query(InsiderTransaction).count() == 1
        assert session.query(Filing).filter_by(processed=True).count() == 2


def test_xslf345_primary_document_is_not_assumed_parseable(monkeypatch, tmp_path):
    with _session(tmp_path) as session:
        filing = _filing("4", "0007")
        filing.primary_document = "xslF345X05/doc.xml"
        session.add(filing)
        session.commit()
        monkeypatch.setattr(
            sec_documents,
            "download_filing_document",
            lambda url: "<XML><notOwnershipDocument /></XML>",
        )

        assert process_sec_filings(session, limit=10, form_type="4") == 1
        assert session.query(FilingEvent).one().needs_human_review is True


def test_8k_parser_ignores_body_item_reference_and_deduplicates():
    rows = parse_8k_items(
        "Item 5.02 Departure of Directors or Certain Officers; Election of Directors; "
        "Appointment of Certain Officers; Compensatory Arrangements of Certain Officers. "
        "The CFO resigned. Under Item 5.02(c)(3), related compensation details are "
        "described below."
    )
    assert [row["sec_item"] for row in rows] == ["Item 5.02"]


def test_8k_item_507_ethics_ai_data_acquisition_proposal_stays_low():
    rows = parse_8k_items(
        "Item 5.07 Submission of Matters to a Vote of Security Holders. "
        "Shareholders voted on a proposal titled Report on Ethical AI Data Acquisition "
        "and Usage."
    )
    assert rows[0]["importance"] == "low"


def test_8k_item_507_merger_vote_is_medium():
    rows = parse_8k_items(
        "Item 5.07 Submission of Matters to a Vote of Security Holders. "
        "Shareholders approved the merger agreement and related business combination."
    )
    assert rows[0]["importance"] in {"medium", "high"}


def test_inline_xbrl_html_extraction_keeps_visible_8k_text_and_drops_noise():
    html = """
    <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
      <head>
        <script>var technicalNoise = true;</script>
        <style>.hidden { display: none; }</style>
      </head>
      <body>
        <ix:header>
          <ix:hidden>
            <ix:nonNumeric name="dei:EntityTradingSymbol">ABC</ix:nonNumeric>
            <ix:nonNumeric name="dei:DocumentPeriodEndDate">true</ix:nonNumeric>
            us-gaap:LongTermDebtMember
            http://fasb.org/us-gaap/2026
            NASDAQ
          </ix:hidden>
        </ix:header>
        <div style="display:none">false dei:AmendmentFlag NYSE</div>
        <h2>Item 2.02 Results of Operations and Financial Condition.</h2>
        <p>The company reported revenue growth and margin expansion in the quarter.</p>
        <p>Exhibit 99.1 Press release dated June 25, 2026.</p>
        <p>SIGNATURES</p>
      </body>
    </html>
    """

    text = sec_documents.extract_text(html)

    assert "Item 2.02" in text
    assert "reported revenue growth and margin expansion" in text
    assert "Exhibit 99.1" in text
    assert "SIGNATURES" in text
    assert "technicalNoise" not in text
    assert "EntityTradingSymbol" not in text
    assert "LongTermDebtMember" not in text
    assert "DocumentPeriodEndDate" not in text
    assert "NASDAQ" not in text
    assert "NYSE" not in text
    assert "true" not in text.lower()
    assert "false" not in text.lower()


def test_process_8k_stores_clean_text_and_preserves_raw_html(monkeypatch, tmp_path):
    html = """<html><body><ix:hidden>false dei:EntityCommonStockSharesOutstanding</ix:hidden>
    <h2>Item 2.02 Results of Operations and Financial Condition.</h2>
    <p>The visible business paragraph explains quarterly results.</p>
    <p>SIGNATURES</p></body></html>"""
    with _session(tmp_path) as session:
        filing = _filing("8-K", "0008")
        filing.primary_document = "doc.htm"
        filing.filing_url = "https://www.sec.gov/Archives/doc.htm"
        session.add(filing)
        session.commit()
        monkeypatch.setattr(
            sec_documents,
            "download_filing_document",
            lambda url: sec_documents.DownloadedFilingDocument(html, url, "text/html", 200),
        )

        assert process_sec_filings(session, limit=10, form_type="8-K") == 1
        doc = session.query(FilingDocument).one()
        assert doc.raw_html == html
        assert doc.raw_xml is None
        assert "Item 2.02" in doc.raw_text
        assert "visible business paragraph" in doc.raw_text
        assert "EntityCommonStockSharesOutstanding" not in doc.raw_text
        event = session.query(FilingEvent).one()
        assert event.sec_item == "Item 2.02"
