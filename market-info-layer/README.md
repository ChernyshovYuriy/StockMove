# Market Info Layer

A local Python 3.11+ market information system for collecting, storing, and summarizing market-relevant information for a user-maintained watchlist.

This project is for research and journaling only. It is not financial advice and it is not trading automation.

## What it does

- Initializes a local SQLite database.
- Loads a YAML watchlist.
- Collects deterministic SEC EDGAR filing metadata for watchlist tickers.
- Collects configured FRED macro observations.
- Collects current Nasdaq trading halt data defensively.
- Generates a daily Markdown brief.
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

## Example commands

```bash
python -m market_info_layer.cli init-db
python -m market_info_layer.cli load-watchlist
python -m market_info_layer.cli collect-sec
python -m market_info_layer.cli collect-macro
python -m market_info_layer.cli collect-halts
python -m market_info_layer.cli collect-all
python -m market_info_layer.cli daily-brief
python -m market_info_layer.cli dashboard
```

## Configuration

- `config/watchlist.yaml` stores tickers, CIKs, and human-maintained thesis fields.
- `config/macro_series.yaml` stores FRED series IDs. Defaults include CPIAUCSL, PCEPI, PAYEMS, UNRATE, FEDFUNDS, DGS10, DGS2, GDPC1, and ICSA.

## Database schema summary

- `tickers`: ticker reference data including CIK and active flag.
- `watchlist`: user-maintained research notes and status.
- `filings`: SEC filing metadata and filing URLs, deduplicated by accession number.
- `macro_events`: dated macro calendar events.
- `macro_observations`: FRED observations with realtime metadata.
- `trading_halts`: Nasdaq halt facts.
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
python -m market_info_layer.cli process-sec-filings --form-type 4 --limit 20
python -m market_info_layer.cli process-sec-filings --form-type 8-K --limit 20
```

The processor preserves the original `filings` rows, downloads each primary SEC document once into `filing_documents`, and stores interpreted data separately in `insider_transactions` and `filing_events`. All downloaded and parsed rows retain the SEC source URL and source `filing_id`.

## Debug export

Create a portable debug package with the SQLite schema, row counts, health checks, CSV table exports, project configuration files, and daily reports. The default export excludes secrets, virtual environments, Git metadata, the full SQLite database, and full raw filing document fields.

```bash
python -m market_info_layer.cli export-debug
python -m market_info_layer.cli export-debug --include-db
python -m market_info_layer.cli export-debug --include-raw-documents
```

