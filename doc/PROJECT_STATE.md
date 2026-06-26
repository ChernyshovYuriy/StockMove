# Project State

## Last updated

2026-06-26

## Current phase

Month 1 / Week 1 — Information Layer Buildout

## Current milestone

SEC EDGAR Block 1 complete for prototype use. The system can collect SEC metadata, process current 8-K documents into filing events, process Form 4 XML into insider transactions, preserve source SEC URLs, and surface unprocessed material filings in the daily brief review queue.

## Latest verified implementation state

- SEC daily routine command: `python -m market_info_layer.cli sec-routine`
- 8-K processing target: `filing_events`
- Form 4 processing target: `insider_transactions`
- Unprocessed material-filing queue: daily brief sections `Unprocessed material filings` and `Needs human review`
- Source auditability: SEC event and insider rows retain SEC source URLs

## Known issues

- 8-K summaries should continue to improve as new filing examples appear.
- 10-Q/10-K deeper extraction remains later work.
- 13D/13G ownership-change interpretation remains later work.
- Remaining Week 1 source blocks still pending: short interest, one press-release/news feed, optional options context, daily checklist/workflow polish.

## Next Codex task

Expand price collection and price-context reporting from the AAPL prototype to all active watchlist tickers, then continue with short-interest checks.

## Commands to reproduce

```bash
python -m market_info_layer.cli sec-routine
python -m market_info_layer.cli daily-brief --processed-today --include-low --style compact
python -m market_info_layer.cli daily-brief --max-unprocessed 10
python -m market_info_layer.cli export-debug --include-db
```
