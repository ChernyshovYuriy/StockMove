# Market Info Layer

A local Python 3.11+ market information system for collecting, storing, and summarizing market-relevant information for a user-maintained watchlist.

This project is for research and journaling only. It is not financial advice and it is not trading automation.

## What it does

- Initializes a local SQLite database.
- Loads a YAML watchlist.
- Collects deterministic SEC EDGAR filing metadata for watchlist tickers.
- Runs a daily SEC routine that processes current 8-Ks into filing events and Form 4s into insider transactions.
- Collects configured FRED macro observations.
- Collects current Nasdaq trading halt data defensively from the Nasdaq Trade Halt RSS feed intended for applications.
- Generates a daily Markdown brief, with optional backfill/review modes for recently processed filings.
- Provides a local Streamlit dashboard for review and manual notes.
- Keeps raw facts separate from interpretation and placeholders for optional future AI analysis.

## What it explicitly does not do

- No automated trading.
- No broker integration.
- No order placement.
- No buy/sell/hold recommendations.
- No position sizing logic.
- No LLM calls in version 1.

## Setup

```bash
cd market-info-layer
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Edit `.env` as needed:

```dotenv
DATABASE_URL=sqlite:///data/market_info_layer.db
SEC_USER_AGENT=MarketInfoLayer contact@example.com
FRED_API_KEY=
```

`SEC_USER_AGENT` should identify your app/contact as expected by SEC access guidance. `FRED_API_KEY` is optional.


## End-to-end verification script

Use the verification script to replace the manual command sequence for initializing the database, collecting data, processing SEC filings, generating review reports, and building the debug ZIP for OpenAI analysis. The script prints timestamped progress before and after every command, stops on the first failure, and leaves the final `export-debug` output path visible in the terminal.

```bash
scripts/run_system_verification.sh
```

The default workflow runs:

```text
init-db -> load-watchlist -> collect-sec -> sec-routine -> collect-macro -> collect-halts -> collect-prices -> process-sec-filings -> daily-brief reports -> export-debug --include-db
```

Common options let you tune the same parameters used in the manual flow:

```bash
scripts/run_system_verification.sh --process-form-type 8-K --process-limit 50 --lookback-days 730
scripts/run_system_verification.sh --export-dir export/openai-debug --include-raw-documents
scripts/run_system_verification.sh --skip-prices --skip-macro --skip-halts
```

Run `scripts/run_system_verification.sh --help` for the full option list.

## Example commands

```bash
python -m market_info_layer.cli init-db
python -m market_info_layer.cli load-watchlist
python -m market_info_layer.cli collect-sec
python -m market_info_layer.cli sec-routine
python -m market_info_layer.cli collect-macro
python -m market_info_layer.cli collect-halts
python -m market_info_layer.cli collect-all
python -m market_info_layer.cli daily-brief
python -m market_info_layer.cli daily-brief --lookback-days 90 --style compact
python -m market_info_layer.cli daily-brief --processed-today --include-low --style debug
python -m market_info_layer.cli daily-brief --max-unprocessed 5
python -m market_info_layer.cli backfill-review
python -m market_info_layer.cli dashboard
```

## Report generation

Daily briefs default to true daily production behavior: parsed filing events are included when their `event_date` equals the report date. During initial SEC backfills or review sessions, use lookback or processed-date options to surface older filing events that were parsed today.

```bash
python -m market_info_layer.cli daily-brief
python -m market_info_layer.cli daily-brief --lookback-days 90 --style compact
python -m market_info_layer.cli daily-brief --processed-today --include-low --style debug
python -m market_info_layer.cli daily-brief --max-unprocessed 5
python -m market_info_layer.cli backfill-review
```

Use `--date YYYY-MM-DD` to choose the report date and `--output-name TEXT` to write a custom Markdown filename under the daily reports directory. Processed/backfill reports label processing time separately from company event dates and use processing-focused sections such as "Events processed in this run". Daily briefs default to `--style compact`, which emits short parsed-event blocks with importance, ticker, event date, SEC item, event type, a summary capped at 240 characters, and the source URL. Human-readable event sections are capped by configurable `--max-events` (default 25); when more events are selected, a sibling `*-events.md` appendix contains the complete selected-event list. Use `--style debug` for the verbose field-rich event format. Low-importance parsed filing events are separated by default in compact reports, and Item 9.01 exhibit-only events are hidden unless `--include-low` is passed. Unprocessed material filings are limited to 10 by default; use `--max-unprocessed INTEGER` to adjust the review list length.


## Nasdaq trade halt troubleshooting

Nasdaq halt times are treated as Eastern Time (`America/New_York`). If an RSS item does not provide an explicit halt date, the collector uses the RSS item publication date as the halt date; if that is unavailable, it falls back to the collection date so historical halt records still receive a date-bearing `halt_datetime`.


`collect-halts` reads Nasdaq's application RSS endpoint:

```bash
curl -i -H 'Accept: application/rss+xml, application/xml, text/xml, */*' \
  -H 'User-Agent: MarketInfoLayer contact@example.com' \
  'https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts'
```

If Nasdaq has no active halt items, the collector should complete successfully and report `Inserted 0 trading halts`; an empty RSS feed is not a failure. If the endpoint returns HTML, an empty error page, or `Oops! That didn't work`, inspect the HTTP status, `Content-Type`, and short response preview before retrying.

## Configuration

- `config/watchlist.yaml` stores tickers, CIKs, and human-maintained thesis fields.
- `config/macro_series.yaml` stores FRED series IDs. Defaults include CPIAUCSL, PCEPI, PAYEMS, UNRATE, FEDFUNDS, DGS10, DGS2, GDPC1, and ICSA.

## Database schema summary

- `tickers`: ticker reference data including CIK and active flag.
- `watchlist`: user-maintained research notes and status.
- `filings`: SEC filing metadata and filing URLs, deduplicated by accession number.
- `macro_events`: dated macro calendar events.
- `macro_observations`: FRED observations with realtime metadata.
- `trading_halts`: Nasdaq halt facts collected from the Nasdaq Trade Halt RSS feed (`https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts`).
- `prices`: daily price bars from isolated providers.
- `daily_notes`: human-authored journal notes.
- `ai_analysis_placeholder`: separated optional future interpretation layer.

Every external record includes a source and collection timestamp.

## First-month roadmap

1. Harden collectors with more fixture coverage and schema validation.
2. Add import/export tools for daily notes.
3. Add more deterministic macro calendar sources.
4. Add optional isolated price provider implementations.
5. Improve Streamlit filtering and report navigation.
6. Add data-quality checks and stale-data warnings.

## Development checks

```bash
pytest
ruff check .
```


## Processing SEC filing documents

After collecting SEC filing metadata with `collect-sec`, parse selected filing documents into normalized event tables:

```bash
python -m market_info_layer.cli process-sec-filings --limit 50
python -m market_info_layer.cli process-sec-filings --form-type 4 --limit 50
python -m market_info_layer.cli process-sec-filings --form-type 8-K --limit 20
python -m market_info_layer.cli sec-routine --limit-per-form 500
```

The processor preserves the original `filings` rows, downloads each primary SEC document once into `filing_documents`, and stores interpreted data separately in `insider_transactions` and `filing_events`. All downloaded and parsed rows retain the SEC source URL and source `filing_id`. HTML filings also keep the exact downloaded HTML in `filing_documents.raw_html` for audit review, while `filing_documents.raw_text` contains cleaned visible text with inline-XBRL metadata stripped.

To regenerate 8-K filing documents and parsed events after text-extraction or importance-classification changes, reset the derived SEC document tables and 8-K processing flag, then rerun processing and review exports:

```sql
DELETE FROM filing_events;
DELETE FROM filing_documents;
UPDATE filings SET processed = 0 WHERE form_type = '8-K';
```

```bash
python -m market_info_layer.cli process-sec-filings --form-type 8-K --limit 50
python -m market_info_layer.cli daily-brief --processed-today --include-low --output-name processed-today-clean-text
python -m market_info_layer.cli daily-brief --lookback-days 730 --include-low --output-name backfill-review-clean-text
python -m market_info_layer.cli export-debug --include-db
```

## Debug export

Create a portable debug package with the SQLite schema, row counts, health checks, CSV table exports, project configuration files, and daily reports. The default export excludes secrets, virtual environments, Git metadata, the full SQLite database, and full raw filing document fields.

```bash
python -m market_info_layer.cli export-debug
python -m market_info_layer.cli export-debug --include-db
python -m market_info_layer.cli export-debug --include-raw-documents
```


### Price collection

Collect daily OHLCV bars for active watchlist tickers:

```bash
python -m market_info_layer.cli collect-prices
python -m market_info_layer.cli collect-prices --ticker AAPL --period 2y
```

The price collector stores one row per ticker, trading date, and source to avoid duplicate rows when the command is rerun. Reports describe price reaction around events as same-period movement requiring human review, not as causation or trading advice.

## Report modes and data-health checks

Daily Markdown reports now distinguish event-date selection from processing/backfill selection:

- `--report-mode event_date` (default) selects filing events whose actual `event_date` falls in the requested date or lookback range. This is the mode to use for a true daily market report.
- `--report-mode processed_at` selects events created or downloaded during the requested processing window. This is intended for backfill review and is titled **Events Processed During Backfill**.
- The legacy `--processed-today` flag is treated as processed/backfill mode for compatibility.

Price context is only attached when the event date has a nearby baseline trading price. The default maximum baseline gap is five calendar days, so older SEC events are not paired with the first available price row years later. Reports state when price context is unavailable or incomplete instead of labeling those cases as human-review flags.

Debug exports include expanded `health_checks.json` issues for watchlist SEC coverage, price coverage, latest filing/price/halt rows, unknown Form 4 transaction codes, placeholder watchlist thesis fields, and filing events that predate available price history. Each issue includes a severity such as `OK`, `WARN`, or `ERROR` where applicable.

## Debug export CSV note

Debug export CSV files are written with standards-compliant quoting. Some text fields can contain embedded newlines, so naive line-count tools such as `wc -l` may not match the number of exported data rows. Use a CSV-aware reader when validating row counts.
