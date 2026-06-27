# ruff: noqa: E501, E701
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_info_layer.settings import ROOT_DIR, get_settings

RAW_COLUMN_NAMES = {"raw_text", "raw_xml", "raw_html", "document_text", "content"}
EXCLUDED_DIRS = {".env", ".venv", ".git"}


def database_path() -> Path:
    url = get_settings().resolved_database_url()
    if not url.startswith("sqlite:///"):
        raise ValueError(f"Debug export only supports SQLite database URLs, got {url!r}")
    return Path(url.removeprefix("sqlite:///"))


def create_debug_export(
    output_dir: Path = ROOT_DIR / "export",
    include_db: bool = False,
    include_raw_documents: bool = False,
    limit_rows_per_table: int = 10_000,
) -> Path:
    db_path = database_path()
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database does not exist: {db_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    zip_path = output_dir / f"market-info-debug-{timestamp}.zip"
    suffix = 1
    while zip_path.exists():
        zip_path = output_dir / f"market-info-debug-{timestamp}-{suffix}.zip"
        suffix += 1

    with tempfile.TemporaryDirectory(prefix="market-info-debug-") as tmp:
        stage = Path(tmp)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            tables = _user_tables(conn)
            schema_sql = _schema_sql(conn)
            (stage / "schema.sql").write_text(schema_sql, encoding="utf-8")
            (stage / "tables").mkdir()
            columns_by_table = {table: _columns(conn, table) for table in tables}
            counts = {table: _row_count(conn, table) for table in tables}
            health_checks = _health_checks(conn, tables, columns_by_table)
            samples: dict[str, list[dict[str, Any]]] = {}
            for table in tables:
                samples[table] = _export_table_csv(
                    conn,
                    stage / "tables" / f"{table}.csv",
                    table,
                    columns_by_table[table],
                    include_raw_documents,
                    limit_rows_per_table,
                    sample_limit=3,
                )
            summary = {
                "created_at": datetime.now(UTC).isoformat(),
                "database_path": str(db_path),
                "tables": tables,
                "row_counts": counts,
                "columns": columns_by_table,
                "sample_rows": samples,
                "health_checks": health_checks,
            }
            _write_json(stage / "summary.json", summary)
            _write_json(stage / "health_checks.json", health_checks)
        finally:
            conn.close()

        _copy_configs(stage / "config")
        _copy_daily_reports(stage / "daily_reports")
        if include_db:
            db_dest = stage / db_path.name
            shutil.copy2(db_path, db_dest)
            if not include_raw_documents:
                summary["sanitized_db"] = _sanitize_sqlite_raw_columns(db_dest)
                _write_json(stage / "summary.json", summary)
        _write_readme(
            stage / "README_DEBUG_EXPORT.md",
            include_db,
            include_raw_documents,
            limit_rows_per_table,
        )
        _zip_dir(stage, zip_path)
    return zip_path


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def _schema_sql(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type IN ('table','index','trigger','view') AND name NOT LIKE 'sqlite_%' "
        "AND sql IS NOT NULL "
        "ORDER BY type, name"
    ).fetchall()
    return "\n\n".join(row["sql"].rstrip(";") + ";" for row in rows) + "\n"


def _columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")]


def _column_names(column_info: list[dict[str, Any]]) -> list[str]:
    return [col["name"] for col in column_info]


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(
        conn.execute(f"SELECT COUNT(*) AS count FROM {_quote_ident(table)}").fetchone()["count"]
    )


def _is_raw_column(name: str) -> bool:
    return name.lower() in RAW_COLUMN_NAMES


def _raw_field_summary(col: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {
            f"{col}_original_length": None,
            f"{col}_sha256": None,
            f"{col}_preview": None,
        }
    if isinstance(value, bytes):
        data = value
        preview = value[:500].decode("utf-8", errors="replace")
    else:
        text = str(value)
        data = text.encode("utf-8")
        preview = text[:500]
    return {
        f"{col}_original_length": len(data),
        f"{col}_sha256": hashlib.sha256(data).hexdigest(),
        f"{col}_preview": preview,
    }


def _export_fieldnames(columns: list[str], include_raw: bool) -> list[str]:
    fieldnames: list[str] = []
    for col in columns:
        if not include_raw and _is_raw_column(col):
            fieldnames.extend([f"{col}_original_length", f"{col}_sha256", f"{col}_preview"])
        else:
            fieldnames.append(col)
    return fieldnames


def _sanitize_row(row: sqlite3.Row, columns: list[str], include_raw: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in columns:
        value = row[col]
        if not include_raw and _is_raw_column(col):
            out.update(_raw_field_summary(col, value))
        else:
            out[col] = value
    return out


def _export_table_csv(
    conn: sqlite3.Connection,
    path: Path,
    table: str,
    column_info: list[dict[str, Any]],
    include_raw: bool,
    limit: int,
    sample_limit: int,
) -> list[dict[str, Any]]:
    columns = _column_names(column_info)
    rows = conn.execute(f"SELECT * FROM {_quote_ident(table)} LIMIT ?", (limit,)).fetchall()
    sanitized = [_sanitize_row(row, columns, include_raw) for row in rows]
    fieldnames = _export_fieldnames(columns, include_raw)
    if sanitized:
        for row in sanitized:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sanitized)
    return sanitized[:sample_limit]


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _copy_configs(dest: Path) -> None:
    src = ROOT_DIR / "config"
    if src.exists():
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*EXCLUDED_DIRS))
    else:
        dest.mkdir()


def _copy_daily_reports(dest: Path) -> None:
    src = ROOT_DIR / "reports" / "daily"
    if src.exists():
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*EXCLUDED_DIRS))
    else:
        dest.mkdir()


def _write_readme(path: Path, include_db: bool, include_raw: bool, limit: int) -> None:
    path.write_text(
        f"""# Debug Export

This package contains a portable snapshot for reviewing the market-info-layer pipeline.

Included files:
- `schema.sql`: SQLite table schema from `sqlite_master`.
- `summary.json`: table list, row counts, columns, sample rows, and health checks.
- `health_checks.json`: project-specific aggregate, duplicate, orphan, and latest-date checks.
- `tables/*.csv`: per-table CSV exports capped at {limit} rows per table.
- `config/`: non-secret project configuration files when present.
- `daily_reports/`: copied daily reports when present.

Intentionally excluded:
- `.env`, `.venv`, `.git`, API keys, and secrets.
- The full SQLite database unless `--include-db` is used. Included here: {include_db}.
- Full raw document/text/XML fields unless `--include-raw-documents` is used.
  Included as separate CSV/raw fields here: {include_raw}.
- Full SQLite database included: {include_db}. Raw document columns in the copied DB are sanitized: {include_db and not include_raw}.

Inspect CSVs with a spreadsheet, Python, or command-line tools. CSV fields may contain quoted embedded newlines, so naive line counts (for example `wc -l`) can be misleading. Inspect `schema.sql`
with any text editor or SQLite client.

Reproduce with:

```bash
python -m market_info_layer.cli export-debug
python -m market_info_layer.cli export-debug --include-db
python -m market_info_layer.cli export-debug --include-raw-documents
```
""",
        encoding="utf-8",
    )


def _zip_dir(source: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            zf.write(path, path.relative_to(source))


def _has(
    tables: list[str], columns: dict[str, list[dict[str, Any]]], table: str, *cols: str
) -> bool:
    return table in tables and set(cols).issubset(set(_column_names(columns[table])))


def _group_count(conn: sqlite3.Connection, table: str, column: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            f"SELECT {_quote_ident(column)} AS value, COUNT(*) AS count "
            f"FROM {_quote_ident(table)} "
            f"GROUP BY {_quote_ident(column)} ORDER BY count DESC, value"
        )
    ]


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def _health_checks(
    conn: sqlite3.Connection, tables: list[str], columns: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "counts": {},
        "orphans": {},
        "duplicates": {},
        "latest_dates": {},
        "missing": {},
        "issues": [],
        "samples": {},
    }
    for table, col in [
        ("filings", "ticker"),
        ("filings", "form_type"),
        ("insider_transactions", "ticker"),
        ("insider_transactions", "transaction_type"),
        ("trading_halts", "ticker"),
        ("macro_observations", "series_id"),
        ("prices", "ticker"),
    ]:
        if _has(tables, columns, table, col):
            checks["counts"][f"{table}_by_{col}"] = _group_count(conn, table, col)
    if _has(tables, columns, "filings", "processed"):
        checks["counts"]["filings_processed"] = _group_count(conn, "filings", "processed")
    if _has(tables, columns, "filings", "processing_status"):
        checks["counts"]["filings_by_processing_status"] = _group_count(conn, "filings", "processing_status")
        null_status = _scalar(conn, "SELECT COUNT(*) FROM filings WHERE processing_status IS NULL OR processing_status = ''")
        if null_status:
            checks["issues"].append({"severity": "ERROR", "code": "filings_null_processing_status", "count": null_status})
    if _has(tables, columns, "filing_documents", "content_type"):
        checks["counts"]["filing_documents_by_content_type"] = _group_count(
            conn, "filing_documents", "content_type"
        )
    if _has(tables, columns, "filing_documents", "http_status_code"):
        checks["counts"]["filing_documents_by_http_status_code"] = _group_count(
            conn, "filing_documents", "http_status_code"
        )
    for col in ("form_type", "sec_item", "event_type", "importance"):
        if _has(tables, columns, "filing_events", col):
            checks["counts"][f"filing_events_by_{col}"] = _group_count(conn, "filing_events", col)
    if _has(tables, columns, "daily_notes", "note_date"):
        checks["counts"]["daily_notes_by_date"] = _group_count(conn, "daily_notes", "note_date")
    for table in ("filing_documents", "filing_events", "insider_transactions"):
        if _has(tables, columns, table, "filing_id") and "filings" in tables:
            checks["orphans"][f"{table}_without_matching_filings"] = _scalar(
                conn,
                f"SELECT COUNT(*) FROM {_quote_ident(table)} t "
                "LEFT JOIN filings f ON t.filing_id = f.id "
                "WHERE f.id IS NULL",
            )
    if _has(tables, columns, "filings", "accession_number"):
        checks["duplicates"]["filings_by_accession_number"] = [
            dict(r)
            for r in conn.execute(
                "SELECT accession_number, COUNT(*) AS count FROM filings "
                "GROUP BY accession_number HAVING COUNT(*) > 1"
            )
        ]
    if _has(tables, columns, "filing_documents", "filing_id"):
        checks["duplicates"]["filing_documents_by_filing_id"] = [
            dict(r)
            for r in conn.execute(
                "SELECT filing_id, COUNT(*) AS count FROM filing_documents "
                "GROUP BY filing_id HAVING COUNT(*) > 1"
            )
        ]
    if _has(tables, columns, "filing_events", "event_hash"):
        null_hashes = _scalar(conn, "SELECT COUNT(*) FROM filing_events WHERE event_hash IS NULL OR event_hash = ''")
        checks["missing"]["filing_events_missing_event_hash"] = null_hashes
        if null_hashes:
            checks["issues"].append({"severity": "ERROR", "code": "filing_events_null_event_hash", "count": null_hashes})
        checks["duplicates"]["filing_events_by_event_hash"] = [dict(r) for r in conn.execute("SELECT event_hash, COUNT(*) AS count FROM filing_events WHERE event_hash IS NOT NULL GROUP BY event_hash HAVING COUNT(*) > 1")]
    if _has(tables, columns, "filing_events", "filing_id", "sec_item", "event_type"):
        checks["duplicates"]["filing_events_by_filing_item_type"] = [
            dict(r)
            for r in conn.execute(
                "SELECT filing_id, sec_item, event_type, COUNT(*) AS count FROM filing_events "
                "GROUP BY filing_id, sec_item, event_type HAVING COUNT(*) > 1"
            )
        ]
    if _has(
        tables,
        columns,
        "insider_transactions",
        "filing_id",
        "owner_name",
        "transaction_date",
        "transaction_code",
        "shares",
        "price",
        "direct_or_indirect",
        "shares_owned_after",
    ):
        checks["duplicates"]["insider_transactions_by_filing_transaction"] = [
            dict(r)
            for r in conn.execute(
                "SELECT filing_id, owner_name, transaction_date, transaction_code, shares, "
                "price, direct_or_indirect, shares_owned_after, COUNT(*) AS count "
                "FROM insider_transactions "
                "GROUP BY filing_id, owner_name, transaction_date, transaction_code, shares, "
                "price, direct_or_indirect, shares_owned_after HAVING COUNT(*) > 1"
            )
        ]
    if _has(tables, columns, "prices", "ticker", "price_date"):
        checks["latest_dates"]["latest_price_date_by_ticker"] = [
            dict(r)
            for r in conn.execute(
                "SELECT ticker, MAX(price_date) AS latest_price_date "
                "FROM prices GROUP BY ticker ORDER BY ticker"
            )
        ]
    if _has(tables, columns, "prices", "ticker", "price_date", "is_complete"):
        checks["latest_dates"]["latest_complete_price_date_by_ticker"] = [
            dict(r)
            for r in conn.execute(
                "SELECT ticker, MAX(price_date) AS latest_complete_price_date "
                "FROM prices WHERE is_complete = 1 GROUP BY ticker ORDER BY ticker"
            )
        ]
        checks["counts"]["incomplete_prices_by_ticker"] = [
            dict(r)
            for r in conn.execute(
                "SELECT ticker, COUNT(*) AS count FROM prices "
                "WHERE is_complete = 0 GROUP BY ticker ORDER BY ticker"
            )
        ]
    if _has(tables, columns, "prices", "ticker", "price_date", "source"):
        checks["duplicates"]["prices_by_ticker_price_date_source"] = [
            dict(r)
            for r in conn.execute(
                "SELECT ticker, price_date, source, COUNT(*) AS count FROM prices "
                "GROUP BY ticker, price_date, source HAVING COUNT(*) > 1"
            )
        ]
    if _has(tables, columns, "macro_observations", "series_id", "observation_date"):
        checks["latest_dates"]["latest_macro_observation_date_by_series_id"] = [
            dict(r)
            for r in conn.execute(
                "SELECT series_id, MAX(observation_date) AS latest_observation_date "
                "FROM macro_observations GROUP BY series_id ORDER BY series_id"
            )
        ]
    if _has(tables, columns, "trading_halts", "halt_date"):
        checks["counts"]["trading_halts_by_halt_date"] = [
            dict(r)
            for r in conn.execute(
                "SELECT halt_date, COUNT(*) AS count FROM trading_halts "
                "GROUP BY halt_date ORDER BY halt_date DESC"
            )
        ]
    if _has(tables, columns, "trading_halts", "ticker"):
        checks["counts"]["trading_halts_by_ticker"] = [
            dict(r)
            for r in conn.execute(
                "SELECT ticker, COUNT(*) AS count FROM trading_halts "
                "GROUP BY ticker ORDER BY count DESC, ticker"
            )
        ]
    if _has(tables, columns, "trading_halts", "halt_datetime"):
        latest_halt = conn.execute(
            "SELECT halt_datetime, halt_time, timezone AS halt_timezone, "
            "halt_time AS halt_local_time "
            "FROM trading_halts WHERE halt_datetime IS NOT NULL AND halt_datetime != '' "
            "ORDER BY halt_datetime DESC LIMIT 1"
        ).fetchone()
        if latest_halt:
            checks["latest_dates"]["latest_halt"] = dict(latest_halt)
            checks["latest_dates"]["latest_halt_datetime"] = latest_halt["halt_datetime"]
        checks["missing"]["trading_halts_missing_halt_datetime"] = _scalar(
            conn,
            "SELECT COUNT(*) FROM trading_halts WHERE halt_datetime IS NULL OR halt_datetime = ''",
        )
    if _has(tables, columns, "trading_halts", "reason_text"):
        checks["missing"]["trading_halts_missing_reason_text"] = _scalar(
            conn, "SELECT COUNT(*) FROM trading_halts WHERE reason_text IS NULL OR reason_text = ''"
        )
    if _has(tables, columns, "trading_halts", "halt_time") and checks["latest_dates"].get(
        "latest_halt"
    ):
        checks["latest_dates"]["latest_halt_time"] = checks["latest_dates"]["latest_halt"].get(
            "halt_time"
        )

    if "watchlist" in tables:
        watch_cols = {c["name"] for c in columns.get("watchlist", [])}
        watch_tickers = (
            [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist ORDER BY ticker")]
            if "ticker" in watch_cols
            else []
        )
        for ticker in watch_tickers:
            if "filings" in tables:
                filing_count = _scalar(
                    conn, "SELECT COUNT(*) FROM filings WHERE ticker = ?", (ticker,)
                )
                cik = conn.execute("SELECT cik FROM tickers WHERE ticker = ?", (ticker,)).fetchone()["cik"] if "tickers" in tables and _has(tables, columns, "tickers", "ticker", "cik") else None
                if filing_count == 0:
                    checks["issues"].append(
                        {
                            "severity": "ERROR" if cik else "WARN",
                            "code": "active_watchlist_cik_zero_sec_filings" if cik else "watchlist_zero_sec_filings",
                            "ticker": ticker,
                            "cik": cik,
                            "message": f"{ticker} has zero SEC filings" + (" despite configured CIK" if cik else ""),
                        }
                    )
                latest = conn.execute(
                    "SELECT MAX(filing_date) AS latest FROM filings WHERE ticker = ?", (ticker,)
                ).fetchone()["latest"]
                checks["latest_dates"].setdefault("latest_filing_date_by_ticker", []).append(
                    {"ticker": ticker, "latest_filing_date": latest}
                )
            if "prices" in tables:
                price_count = _scalar(
                    conn, "SELECT COUNT(*) FROM prices WHERE ticker = ?", (ticker,)
                )
                if price_count == 0:
                    checks["issues"].append(
                        {
                            "severity": "ERROR",
                            "code": "watchlist_zero_price_rows",
                            "ticker": ticker,
                            "message": f"{ticker} has zero price rows",
                        }
                    )
        placeholder_values = (
            "Example watchlist entry",
            "Example research thesis",
            "Example invalidation condition",
            "Example catalyst",
        )
        for field in ("reason_watching", "thesis", "invalidation_condition", "catalyst"):
            if field in watch_cols:
                placeholder_sql = ",".join("?" for _ in placeholder_values)
                sql = (
                    f"SELECT ticker, {field} AS value FROM watchlist "
                    f"WHERE {field} IN ({placeholder_sql})"
                )
                for r in conn.execute(sql, placeholder_values):
                    checks["issues"].append(
                        {
                            "severity": "WARN",
                            "code": "watchlist_placeholder_text",
                            "ticker": r["ticker"],
                            "field": field,
                            "message": f"{r['ticker']} has placeholder {field}",
                        }
                    )
    if _has(tables, columns, "insider_transactions", "transaction_code"):
        known = ("P", "S", "A", "D", "F", "G", "M", "C", "X", "J")
        placeholders = ",".join("?" for _ in known)
        unknown = [
            dict(r)
            for r in conn.execute(
                "SELECT transaction_code, COUNT(*) AS count FROM insider_transactions "
                f"WHERE COALESCE(transaction_code,'') NOT IN ({placeholders}) "
                "GROUP BY transaction_code",
                known,
            )
        ]
        checks["counts"]["unknown_insider_transaction_codes"] = unknown
        for row in unknown:
            checks["issues"].append(
                {"severity": "WARN", "code": "unknown_insider_transaction_code", **row}
            )
    if _has(tables, columns, "filing_events", "ticker", "event_date") and _has(
        tables, columns, "prices", "ticker", "price_date"
    ):
        preprice_sql = """
            SELECT e.id, e.ticker, e.event_date, m.min_price_date
            FROM filing_events e
            JOIN (SELECT ticker, MIN(price_date) AS min_price_date FROM prices GROUP BY ticker) m
              ON m.ticker = e.ticker
            WHERE e.event_date IS NOT NULL AND date(e.event_date) < date(m.min_price_date)
        """
        total = int(conn.execute(f"SELECT COUNT(*) AS count FROM ({preprice_sql})").fetchone()["count"])
        by_ticker = [dict(r) for r in conn.execute(f"SELECT q.ticker, COUNT(*) AS count FROM ({preprice_sql}) q GROUP BY q.ticker ORDER BY q.ticker")]
        sample = [dict(r) for r in conn.execute(preprice_sql + " ORDER BY e.ticker, e.event_date LIMIT 100")]
        checks["counts"]["event_predates_price_history_total"] = total
        checks["counts"]["event_predates_price_history_by_ticker"] = by_ticker
        checks["samples"]["event_predates_price_history"] = sample
        if total:
            checks["issues"].append({"severity": "WARN", "code": "event_predates_price_history", "count": total, "by_ticker": by_ticker, "sample_size": len(sample)})

    for key, table, col in [
        ("latest_filing_date", "filings", "filing_date"),
        ("latest_filing_event_date", "filing_events", "event_date"),
        ("latest_insider_transaction_date", "insider_transactions", "transaction_date"),
        ("latest_macro_observation_date", "macro_observations", "observation_date"),
    ]:
        if _has(tables, columns, table, col):
            checks["latest_dates"][key] = _scalar(
                conn, f"SELECT MAX({_quote_ident(col)}) FROM {_quote_ident(table)}"
            )
    return checks


def _sanitize_sqlite_raw_columns(db_copy: Path) -> dict[str, Any]:
    size_before = db_copy.stat().st_size
    conn = sqlite3.connect(db_copy)
    conn.row_factory = sqlite3.Row
    try:
        for table in _user_tables(conn):
            cols = [c["name"] for c in _columns(conn, table)]
            raw_cols = [c for c in cols if _is_raw_column(c)]
            if raw_cols:
                assignments = ", ".join(f"{_quote_ident(c)} = NULL" for c in raw_cols)
                conn.execute(f"UPDATE {_quote_ident(table)} SET {assignments}")
        conn.commit()
        conn.execute("VACUUM")
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    finally:
        conn.close()
    size_after = db_copy.stat().st_size
    freelist_pct = (freelist_count / page_count * 100) if page_count else 0.0
    return {"size_before_bytes": size_before, "size_after_bytes": size_after, "freelist_count": freelist_count, "page_count": page_count, "freelist_pct": freelist_pct, "vacuum_ran": True}
