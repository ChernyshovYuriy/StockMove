# Market Info Layer — Current Stage

## Program context

This project belongs to the broader plan:

**Ninety-day plan for interpreting unexpected stock moves**  
from the research direction:  
**Information Asymmetry and Hidden Drivers of Unexpected Stock Moves**

The purpose is to build a local, auditable information layer that helps explain unexpected stock moves by separating:

- known facts
- timing/context
- market reaction
- interpretation
- human review

The project is **not** a trading bot. It does not place trades, connect to brokers, generate buy/sell/hold recommendations, or automate position decisions.

---

## Are we at Week 1, Block 3?

**Mostly yes, but with a correction.**

If Week 1 means:

> Build the information layer: EDGAR routine, macro calendar, halt pages, short-interest checks, one news feed, one options tool, daily dashboard, and a watchlist that explains why each ticker is being watched

then the project is definitely still in **Week 1: Information Layer Buildout**.

However, the implementation has already gone beyond a narrow “Block 3.” A more accurate status is:

> **Week 1 — Blocks 1–3 completed at prototype level, Block 4 also partially/completely implemented, remaining Week 1 sources still pending.**

Because the original blocks were not formally numbered, this document defines the working block map below.

---

## Working Week 1 block map

### Block 1 — SEC EDGAR routine

**Status: Complete for Week 1 prototype.**

Implemented:

- SEC filing metadata collection
- ticker/CIK support
- 8-K document download
- cleaned SEC HTML/inline-XBRL text extraction
- 8-K item parsing into normalized `filing_events`
- Form 4 insider-transaction parsing into normalized `insider_transactions`
- unprocessed material-filing queue in daily briefs
- `sec-routine` command for daily SEC collection plus 8-K/Form 4 processing
- duplicate prevention
- source URL preservation
- debug export verification
- daily/backfill report integration

Latest verified state:

- `filings`: 749
- `filing_documents`: 50
- `filing_events`: 86
- duplicate filings: 0
- duplicate filing documents: 0
- duplicate filing events: 0
- orphan filing documents: 0
- orphan filing events: 0

Remaining SEC work:

- Keep improving 8-K event summaries as new filing patterns appear.
- 10-Q/10-K deeper extraction is not yet implemented.
- 13D/13G ownership-change interpretation is not yet implemented.

---

### Block 2 — Macro calendar / macro context

**Status: Complete enough for prototype.**

Implemented:

- FRED API integration
- macro series configuration
- macro observations stored in SQLite
- latest macro values surfaced in reports
- duplicate prevention

Latest verified state:

- `macro_observations`: 37,917

Configured series:

- CPIAUCSL
- PCEPI
- PAYEMS
- UNRATE
- FEDFUNDS
- DGS10
- DGS2
- GDPC1
- ICSA

Remaining macro work:

- Add release-calendar awareness, not just latest observations.
- Add “macro event today/tomorrow” logic.
- Add surprise/consensus data later only if a reliable source is available.

---

### Block 3 — Trading halt page / halt context

**Status: Complete enough for prototype.**

Implemented:

- Nasdaq halt RSS collection
- halt date/time fields
- halt reason code/text fields
- duplicate prevention
- report integration for watchlist tickers

Latest verified state:

- `trading_halts`: 67
- duplicate trading halts: 0

Remaining halt work:

- Improve strict ISO datetime format.
- Improve halt-reason code mapping from verified Nasdaq halt-code references.
- Add daily filtering so old halt records do not clutter normal reports.

---

### Block 4 — Price/volume context

**Status: Complete enough for prototype.**

Implemented:

- daily OHLCV collection for AAPL
- price table with duplicate prevention
- `is_complete` handling so current-day incomplete candles are not treated as final
- event-window helper
- price reaction context in reports
- conservative wording: “same-period movement,” not causality

Latest verified state:

- `prices`: 501
- latest complete price date: 2026-06-24
- duplicate prices: 0

Remaining price work:

- Expand from AAPL to all watchlist tickers.
- Add market/sector benchmark comparison.
- Add abnormal-volume and relative-move flags.
- Add missing-data checks per ticker.

---

### Block 5 — Dashboard and debug export

**Status: Complete enough for prototype.**

Implemented:

- Streamlit dashboard
- debug export zip
- schema export
- CSV table exports
- health checks
- DB inclusion option
- raw document field hashing/truncation
- daily reports included in exports

Remaining dashboard/report work:

- Compact report mode should become default morning-use format.
- Debug/backfill report can stay verbose.
- Dashboard should become less table-heavy and more “review workflow” oriented.

---

## Current technical state

The system now has a working local evidence pipeline:

```text
SEC filings
  -> cleaned filing documents
  -> parsed 8-K events
  -> parsed Form 4 insider transactions
  -> macro context
  -> trading halt context
  -> price/volume context
  -> daily/backfill reports
  -> debug export for review
```

This is now useful enough to continue the ninety-day plan.

---

## Current decision

Do **not** move to AI interpretation yet.

The right next step is:

> Finish Week 1 by completing the remaining information-layer sources and making the daily workflow usable.

Immediate next work:

1. Run `sec-routine` as the daily SEC review command and monitor the unprocessed material-filing queue.
2. Expand price collection from AAPL to watchlist tickers.
3. Add short-interest source/check.
4. Add one carefully chosen news/press-release feed.
5. Add one options-data source, or explicitly defer it if reliable data requires payment.
6. Create a daily operating checklist.
7. Update `PROJECT_STATE.md` after each milestone.

---

## Current project classification

```text
Plan:        Ninety-day plan for interpreting unexpected stock moves
Phase:       Month 1 / Week 1
Focus:       Build the information layer
Status:      SEC Block 1 complete for prototype; macro + halt + price context working at prototype level
Next:        Finish remaining Week 1 sources and workflow
Risk:        Avoid overbuilding before the daily process is usable
```
