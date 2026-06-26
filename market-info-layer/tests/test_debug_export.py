import csv
import json
import sqlite3
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from market_info_layer.cli import app
from market_info_layer.db.database import init_db
from market_info_layer.settings import get_settings

runner = CliRunner()


def _set_db(monkeypatch, db_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()


def _seed(db_path: Path) -> None:
    init_db(f"sqlite:///{db_path}")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO filings "
        "(ticker,cik,form_type,filing_date,report_date,accession_number,"
        "primary_document,filing_url,source,collected_at,processed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ABC",
            "1",
            "8-K",
            "2026-01-02",
            None,
            "0001",
            "a.htm",
            "https://example.com/a",
            "sec",
            "now",
            1,
        ),
    )
    con.execute(
        "INSERT INTO filing_documents "
        "(filing_id,ticker,form_type,source_url,raw_text,raw_xml,"
        "downloaded_at,http_status_code,content_type) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            1,
            "ABC",
            "8-K",
            "https://example.com/a",
            "X" * 800,
            "<xml>secret</xml>",
            "now",
            200,
            "text/html",
        ),
    )
    con.execute(
        "INSERT INTO filing_events "
        "(filing_id,ticker,form_type,event_date,event_type,sec_item,headline,"
        "summary,importance,source_url,needs_human_review,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            999,
            "ABC",
            "8-K",
            "2026-01-03",
            "earnings",
            "2.02",
            "h",
            "s",
            "high",
            "https://example.com/a",
            0,
            "now",
        ),
    )
    con.commit()
    con.close()


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def test_export_debug_creates_zip_summary_schema_csv_and_excludes_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _set_db(monkeypatch, db_path)
    _seed(db_path)
    (tmp_path / ".env").write_text("SECRET=1")

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    zip_path = Path(result.output.strip())
    assert zip_path.exists()
    names = _zip_names(zip_path)
    assert "summary.json" in names
    assert "schema.sql" in names
    assert "tables/filings.csv" in names
    assert "tables/filing_documents.csv" in names
    assert ".env" not in names


def test_raw_large_fields_are_truncated_and_hashed_by_default(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _set_db(monkeypatch, db_path)
    _seed(db_path)

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    zip_path = Path(result.output.strip())
    with zipfile.ZipFile(zip_path) as zf:
        rows = list(csv.DictReader(zf.read("tables/filing_documents.csv").decode().splitlines()))
    row = rows[0]
    assert "raw_text" not in row
    assert row["raw_text_original_length"] == "800"
    assert len(row["raw_text_sha256"]) == 64
    assert row["raw_text_preview"] == "X" * 500


def test_include_raw_documents_exports_full_raw_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _set_db(monkeypatch, db_path)
    _seed(db_path)

    result = runner.invoke(
        app,
        ["export-debug", "--output-dir", str(tmp_path / "exports"), "--include-raw-documents"],
    )

    zip_path = Path(result.output.strip())
    with zipfile.ZipFile(zip_path) as zf:
        rows = list(csv.DictReader(zf.read("tables/filing_documents.csv").decode().splitlines()))
    assert rows[0]["raw_text"] == "X" * 800
    assert rows[0]["raw_xml"] == "<xml>secret</xml>"


def test_optional_tables_missing_and_health_checks_do_not_crash(tmp_path, monkeypatch):
    db_path = tmp_path / "minimal.db"
    _set_db(monkeypatch, db_path)
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE filings (id INTEGER PRIMARY KEY, accession_number TEXT)")
    con.execute("INSERT INTO filings (accession_number) VALUES ('dup'), ('dup')")
    con.commit()
    con.close()

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(Path(result.output.strip())) as zf:
        health = json.loads(zf.read("health_checks.json"))
    assert health["duplicates"]["filings_by_accession_number"][0]["count"] == 2


def test_command_can_run_twice(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _set_db(monkeypatch, db_path)
    _seed(db_path)

    first = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])
    second = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert Path(first.output.strip()).exists()
    assert Path(second.output.strip()).exists()


def test_include_db_copies_sqlite_database(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _set_db(monkeypatch, db_path)
    _seed(db_path)

    result = runner.invoke(
        app,
        ["export-debug", "--output-dir", str(tmp_path / "exports"), "--include-db"],
    )

    assert result.exit_code == 0, result.output
    assert db_path.name in _zip_names(Path(result.output.strip()))


def test_small_metadata_fields_remain_readable_by_default(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _set_db(monkeypatch, db_path)
    _seed(db_path)

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    zip_path = Path(result.output.strip())
    with zipfile.ZipFile(zip_path) as zf:
        doc_rows = list(
            csv.DictReader(zf.read("tables/filing_documents.csv").decode().splitlines())
        )
        filing_rows = list(csv.DictReader(zf.read("tables/filings.csv").decode().splitlines()))
        event_rows = list(csv.DictReader(zf.read("tables/filing_events.csv").decode().splitlines()))
    assert doc_rows[0]["content_type"] == "text/html"
    assert doc_rows[0]["source_url"] == "https://example.com/a"
    assert filing_rows[0]["primary_document"] == "a.htm"
    assert filing_rows[0]["source"] == "sec"
    assert filing_rows[0]["filing_url"] == "https://example.com/a"
    assert event_rows[0]["source_url"] == "https://example.com/a"


def test_health_check_reports_duplicate_filing_events_by_filing_item_type(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _set_db(monkeypatch, db_path)
    _seed(db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO filing_events "
        "(filing_id,ticker,form_type,event_date,event_type,sec_item,headline,"
        "summary,importance,source_url,needs_human_review,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            999,
            "ABC",
            "8-K",
            "2026-01-03",
            "earnings",
            "2.02",
            "h2",
            "s2",
            "high",
            "https://example.com/a",
            0,
            "now",
        ),
    )
    con.commit()
    con.close()

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(Path(result.output.strip())) as zf:
        health = json.loads(zf.read("health_checks.json"))
    duplicate = health["duplicates"]["filing_events_by_filing_item_type"][0]
    assert duplicate["filing_id"] == 999
    assert duplicate["sec_item"] == "2.02"
    assert duplicate["event_type"] == "earnings"
    assert duplicate["count"] == 2


def _seed_single_raw_column_table(db_path: Path, column_name: str, value: str) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        f"CREATE TABLE debug_raw_{column_name} (id INTEGER PRIMARY KEY, {column_name} TEXT)"
    )
    con.execute(f"INSERT INTO debug_raw_{column_name} ({column_name}) VALUES (?)", (value,))
    con.commit()
    con.close()


def _exported_rows(zip_path: Path, table_name: str) -> list[dict[str, str]]:
    with zipfile.ZipFile(zip_path) as zf:
        return list(csv.DictReader(zf.read(f"tables/{table_name}.csv").decode().splitlines()))


def test_export_debug_works_when_table_has_raw_text_only(tmp_path, monkeypatch):
    db_path = tmp_path / "raw_text.db"
    _set_db(monkeypatch, db_path)
    _seed_single_raw_column_table(db_path, "raw_text", "T" * 700)

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    rows = _exported_rows(Path(result.output.strip()), "debug_raw_raw_text")
    assert "raw_text" not in rows[0]
    assert rows[0]["raw_text_original_length"] == "700"
    assert len(rows[0]["raw_text_sha256"]) == 64
    assert rows[0]["raw_text_preview"] == "T" * 500


def test_export_debug_works_when_table_has_raw_xml(tmp_path, monkeypatch):
    db_path = tmp_path / "raw_xml.db"
    _set_db(monkeypatch, db_path)
    _seed_single_raw_column_table(db_path, "raw_xml", "<root>" + "X" * 600 + "</root>")

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    rows = _exported_rows(Path(result.output.strip()), "debug_raw_raw_xml")
    assert "raw_xml" not in rows[0]
    assert int(rows[0]["raw_xml_original_length"]) > 600
    assert len(rows[0]["raw_xml_sha256"]) == 64
    assert rows[0]["raw_xml_preview"].startswith("<root>")


def test_export_debug_works_when_table_has_raw_html(tmp_path, monkeypatch):
    db_path = tmp_path / "raw_html.db"
    _set_db(monkeypatch, db_path)
    _seed_single_raw_column_table(db_path, "raw_html", "<html>" + "H" * 600 + "</html>")

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    rows = _exported_rows(Path(result.output.strip()), "debug_raw_raw_html")
    assert "raw_html" not in rows[0]
    assert int(rows[0]["raw_html_original_length"]) > 600
    assert len(rows[0]["raw_html_sha256"]) == 64
    assert rows[0]["raw_html_preview"].startswith("<html>")


def test_export_debug_default_mode_does_not_crash_with_mixed_raw_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "mixed.db"
    _set_db(monkeypatch, db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE docs (id INTEGER PRIMARY KEY, raw_text TEXT, raw_xml TEXT, raw_html TEXT)"
    )
    con.execute("INSERT INTO docs (raw_text) VALUES (?)", ("text",))
    con.execute(
        "INSERT INTO docs (raw_xml, raw_html) VALUES (?, ?)",
        ("<xml>later</xml>", "<html>later</html>"),
    )
    con.commit()
    con.close()

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    rows = _exported_rows(Path(result.output.strip()), "docs")
    assert rows[1]["raw_xml_preview"] == "<xml>later</xml>"
    assert rows[1]["raw_html_preview"] == "<html>later</html>"


def test_export_debug_include_raw_documents_does_not_crash_with_mixed_raw_columns(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "mixed_include.db"
    _set_db(monkeypatch, db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE docs (id INTEGER PRIMARY KEY, raw_text TEXT, raw_xml TEXT, raw_html TEXT)"
    )
    con.execute("INSERT INTO docs (raw_text) VALUES (?)", ("text",))
    con.execute(
        "INSERT INTO docs (raw_xml, raw_html) VALUES (?, ?)",
        ("<xml>later</xml>", "<html>later</html>"),
    )
    con.commit()
    con.close()

    result = runner.invoke(
        app,
        ["export-debug", "--output-dir", str(tmp_path / "exports"), "--include-raw-documents"],
    )

    assert result.exit_code == 0, result.output
    rows = _exported_rows(Path(result.output.strip()), "docs")
    assert rows[1]["raw_xml"] == "<xml>later</xml>"
    assert rows[1]["raw_html"] == "<html>later</html>"


def test_export_debug_reports_incomplete_price_counts(tmp_path, monkeypatch):
    db_path = tmp_path / "prices.db"
    _set_db(monkeypatch, db_path)
    init_db(f"sqlite:///{db_path}")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO prices "
        "(ticker,price_date,open,high,low,close,volume,is_complete,source,collected_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("AAPL", "2026-06-24", 1, 1, 1, 1, 100, 1, "mock", "now"),
    )
    con.execute(
        "INSERT INTO prices "
        "(ticker,price_date,open,high,low,close,volume,is_complete,source,collected_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("AAPL", "2026-06-25", 1, 1, 1, 1, 50, 0, "mock", "now"),
    )
    con.commit()
    con.close()

    result = runner.invoke(app, ["export-debug", "--output-dir", str(tmp_path / "exports")])

    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(Path(result.output.strip())) as zf:
        health = json.loads(zf.read("health_checks.json"))
    assert health["counts"]["prices_by_ticker"][0] == {"value": "AAPL", "count": 2}
    assert health["counts"]["incomplete_prices_by_ticker"][0] == {
        "ticker": "AAPL",
        "count": 1,
    }
    assert health["latest_dates"]["latest_complete_price_date_by_ticker"][0] == {
        "ticker": "AAPL",
        "latest_complete_price_date": "2026-06-24",
    }
