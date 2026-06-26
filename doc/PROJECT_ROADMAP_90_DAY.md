# Market Info Layer — Roadmap to Complete the Project

## Mission

Build a local, auditable Python system for interpreting unexpected stock moves.

The system should help answer:

- What happened?
- What information was available before or during the move?
- Was the move company-specific, macro-driven, sector-driven, liquidity-driven, or unexplained?
- What did the price and volume do around the event?
- What did I miss?
- Should this ticker remain on the watchlist?

The project is part of:

**Ninety-day plan for interpreting unexpected stock moves**  
from the broader research direction:  
**Information Asymmetry and Hidden Drivers of Unexpected Stock Moves**

The system must remain a research and journaling tool. It must not become a trading bot.

---

## Operating principles

### 1. Evidence before interpretation

Raw facts must be stored separately from interpretation.

```text
raw source data
  -> normalized facts
  -> derived context
  -> human/AI interpretation
  -> journal/postmortem
```

### 2. Source auditability

Every external record should keep:

- source name
- source URL
- collected timestamp
- original date/time
- normalized fields
- duplicate-prevention key

### 3. Conservative language

The system should say:

- “price reaction around event”
- “same-period movement”
- “possible explanation”
- “needs human review”

It should not say:

- “this caused the move”
- “buy”
- “sell”
- “hold”
- “guaranteed”
- “prediction”

### 4. Python first, AI later

Python should collect and normalize deterministic facts.

AI should later help summarize and classify evidence, but only after the raw-data pipeline is stable.

---

# Month 1 — Build the information layer

## Week 1 — Core evidence pipeline

### Block 1 — SEC EDGAR routine

**Status: Complete for Week 1 prototype.**

Already implemented:

- SEC filing metadata collector
- CIK/ticker support
- 8-K document download
- clean text extraction
- 8-K item parsing
- `filing_events`
- Form 4 insider-transaction parsing into `insider_transactions`
- unprocessed material-filing review queue in daily briefs
- `sec-routine` command that collects metadata and processes current 8-K/Form 4 filings
- reports
- debug export

Remaining work:

- Keep improving 8-K event summaries as new examples appear.
- Add 13D/13G ownership-change interpretation later.
- Add deeper 10-Q/10-K extraction later.

Acceptance criteria:

- SEC collection can run daily via `python -m market_info_layer.cli sec-routine`.
- New 8-Ks appear in the report after processing.
- Duplicate filings and duplicate insider transactions are prevented.
- Every filing event and insider transaction links back to a source SEC URL.
- Unprocessed material filings appear in the daily brief review queue.

---

### Block 2 — Macro context

**Status: Prototype complete.**

Already implemented:

- FRED collector
- macro observations table
- latest macro values in reports

Remaining work:

- Add macro release-calendar awareness.
- Add “macro events today/tomorrow” section.
- Add basic regime flags:
  - rates up/down
  - inflation trend
  - labor trend
  - yield curve context

Acceptance criteria:

- Daily report shows relevant macro context.
- Macro data does not duplicate.
- Missing FRED key fails clearly.

---

### Block 3 — Trading halts

**Status: Prototype complete.**

Already implemented:

- Nasdaq RSS collector
- halt date/time fields
- reason code/text fields
- duplicate prevention
- report integration for watchlist tickers

Remaining work:

- Improve ISO datetime handling.
- Improve reason-code mapping.
- Filter normal daily report to recent/watchlist halts only.

Acceptance criteria:

- `collect-halts` succeeds even if there are no current halts.
- No active halt is treated as a valid zero-result state, not an error.
- Halt records include enough date/time context for later analysis.

---

### Block 4 — Price/volume context

**Status: Prototype complete.**

Already implemented:

- daily OHLCV collection
- `is_complete` field for current-day safety
- event-window price reaction
- concise report output for meaningful events

Remaining work:

- Run prices for all watchlist tickers.
- Add benchmark comparison: SPY/QQQ/sector ETF.
- Add abnormal-volume checks.

Acceptance criteria:

- Event reports show conservative same-period movement.
- Current-day incomplete candles are not treated as complete.
- Duplicate prices are prevented.

---

### Block 5 — Short-interest checks

**Status: Not started.**

Goal:

Add short-interest context for watchlist tickers.

Potential sources:

- FINRA / exchange-published short-interest data
- Nasdaq short-interest pages if available and legally usable
- paid source later only if necessary

Data to store:

- ticker
- settlement date
- publication date
- short interest
- average daily volume
- days to cover
- source
- collected_at

Acceptance criteria:

- Short-interest data is stored with settlement/publication dates.
- Report can flag high/increasing short interest.
- System clearly labels short-interest data as delayed, not real-time.

---

### Block 6 — One news / press-release feed

**Status: Not started.**

Goal:

Add one reliable, low-noise feed.

Preferred order:

1. official company investor-relations press releases
2. SEC 8-K exhibit/press-release documents
3. RSS feeds from known sources
4. paid news API only after need is proven

Avoid:

- random scraped news
- unverified social media
- source-less “market news” feeds

Data to store:

- ticker
- published_at
- headline
- source
- source_url
- summary
- theme
- collected_at

Acceptance criteria:

- News/press-release entries include original source.
- Daily report distinguishes company press releases from media commentary.
- Missing original source is flagged as weak evidence.

---

### Block 7 — One options tool/source

**Status: Not started / optional.**

Goal:

Use options data as market-expectation context, not as a trade signal.

Useful fields:

- implied volatility
- open interest
- unusual volume
- put/call ratio
- expected move around earnings
- expiry concentration

Acceptance criteria:

- Options data is labeled as context only.
- No options-trade recommendations are generated.
- Source limitations are documented.

This block may be deferred if reliable data requires paid access.

---

### Block 8 — Daily dashboard and watchlist reasoning

**Status: Partially implemented.**

Already implemented:

- Streamlit dashboard
- reports
- debug export

Remaining work:

- Convert dashboard from raw tables to review workflow.
- Add daily checklist.
- Add “why watching” discipline.
- Add manual daily note entry.
- Add status fields:
  - active
  - waiting
  - human review
  - thesis invalidated
  - remove

Acceptance criteria:

- Every ticker has a reason for being watched.
- Every daily review produces notes or explicit “no action/no new information.”
- The dashboard shows what needs review first.

---

## Week 2 — Make it operational

Goal:

Turn the information layer into a repeatable daily process.

Tasks:

1. Run daily collectors.
2. Generate morning report.
3. Generate backfill/debug report only when needed.
4. Add manual notes workflow.
5. Add post-close review.
6. Add source freshness checks.
7. Add failure visibility:
   - FRED unavailable
   - SEC unavailable
   - Nasdaq halt feed unavailable
   - price provider unavailable

Acceptance criteria:

- One command can update all data.
- One report shows today’s review items.
- Failures are visible but do not corrupt the database.
- Daily use takes less than 15 minutes.

---

## Week 3 — Event classification and postmortems

Goal:

Start measuring whether the system explains moves better over time.

Tasks:

1. Add event categories:
   - earnings/results
   - leadership
   - guidance
   - capital raise
   - litigation/regulatory
   - ownership change
   - macro-sensitive
   - halt/liquidity event
   - unknown
2. Add postmortem table.
3. Add “what did we know before the move?” report.
4. Add “missed signal” field.
5. Add confidence score.

Acceptance criteria:

- Each notable move has a postmortem.
- Postmortems distinguish known facts from guesses.
- Unknown/unexplained moves are tracked, not hidden.

---

## Week 4 — Watchlist quality and routine

Goal:

Make the watchlist an explanation engine instead of a ticker list.

Tasks:

1. Replace sample watchlist data when ready.
2. Add ticker thesis quality checks.
3. Add invalidation rules.
4. Add catalyst calendar fields.
5. Add “remove from watchlist” process.
6. Add weekly review.

Acceptance criteria:

- Every watched ticker has a thesis and invalidation condition.
- Tickers without a reason are removed.
- Weekly review identifies which signals mattered and which were noise.

---

# Month 2 — Interpretation and feedback loop

## Week 5 — AI interpretation layer

Goal:

Add AI as a junior analyst, not as a trader.

AI tasks:

- summarize filings
- classify events
- identify possible market relevance
- generate human-review questions
- compare event + price + macro context
- produce plain-language summaries

Rules:

- AI output goes into separate tables.
- AI output must include evidence.
- AI output must include confidence.
- AI must not overwrite raw facts.
- AI must not generate buy/sell/hold recommendations.

Acceptance criteria:

- AI summaries are traceable to source records.
- Human can approve/reject AI interpretation.
- AI mistakes can be reviewed later.

---

## Week 6 — Cross-source explanation

Goal:

Connect sources into possible explanations.

Example:

```text
AAPL moved -2.5%
same period:
- 8-K filed
- macro rates moved higher
- no halt
- no obvious company-specific bad news
- volume below 20-day average

classification:
likely macro/market context, not filing-specific
confidence: medium
```

Acceptance criteria:

- Reports can show source combinations.
- System avoids false causality.
- “Unknown” remains a valid explanation.

---

## Week 7 — Sector and benchmark context

Goal:

Avoid explaining every move as company-specific.

Add:

- SPY / QQQ benchmark comparison
- sector ETF comparison
- relative performance
- beta-like context
- same-day peer moves

Acceptance criteria:

- Report can say whether move was stock-specific or market-wide.
- Watchlist moves are compared against benchmark movement.

---

## Week 8 — Weekly learning review

Goal:

Measure progress.

Add:

- weekly summary
- top explained moves
- unexplained moves
- missed signals
- false alarms
- source usefulness ranking

Acceptance criteria:

- Weekly report says what information sources helped.
- The system improves the process, not just data volume.

---

# Month 3 — Robustness, scale, and decision support

## Week 9 — Multi-ticker scaling

Goal:

Move from AAPL prototype to real watchlist.

Tasks:

- collect SEC for all watchlist tickers
- collect prices for all watchlist tickers
- process 8-Ks for all watchlist tickers
- run reports across all tickers
- improve performance

Acceptance criteria:

- System handles 10–30 tickers reliably.
- Reports remain readable.

---

## Week 10 — Source quality scoring

Goal:

Rank evidence quality.

Possible levels:

- official filing
- official company release
- government macro source
- exchange/Nasdaq source
- licensed news source
- weak vendor-only source
- unknown source

Acceptance criteria:

- Every explanation shows evidence quality.
- Low-quality evidence cannot dominate the report.

---

## Week 11 — Review dashboard

Goal:

Build a proper review UI.

Dashboard should show:

- today’s events
- high-priority filings
- macro context
- halted tickers
- unusual price/volume reactions
- postmortem queue
- watchlist status
- unresolved questions

Acceptance criteria:

- Dashboard supports daily review without needing raw SQL.
- Reports and UI agree.

---

## Week 12 — Ninety-day assessment

Goal:

Decide whether the system is useful.

Questions:

- Did it explain unexpected moves better than before?
- Which sources mattered most?
- Which sources were noise?
- Which classifications were wrong?
- Did the dashboard reduce confusion?
- Is it worth extending to paid data?
- Is AI interpretation useful or premature?

Deliverables:

- final 90-day review
- source usefulness ranking
- next-quarter roadmap
- decision on paid data/news/options feed
- decision on automation level

---

# Current recommended next actions

## Immediate next action

Continue Week 1 unfinished blocks now that the SEC EDGAR routine is complete for prototype use.

## Next after SEC Block 1


1. Run `sec-routine` during daily review and monitor the unprocessed material-filing queue.
2. Expand price collection from AAPL to all watchlist tickers.
3. Short-interest checks.
4. One reliable press-release/news feed.
5. Optional options context.
6. Daily checklist and watchlist review workflow.

## Do not do yet

Avoid these until Week 5+:

- AI interpretation layer
- prediction ranking
- automated alerts based on AI opinion
- broker integration
- options strategy automation
- paid feeds unless a clear bottleneck appears

---

# Repo files to add

Add these markdown files to the Git repo root:

```text
PROJECT_CURRENT_STAGE.md
PROJECT_ROADMAP_90_DAY.md
PROJECT_STATE.md
```

Suggested use:

- `PROJECT_CURRENT_STAGE.md`: stable snapshot of where the project is now.
- `PROJECT_ROADMAP_90_DAY.md`: long roadmap.
- `PROJECT_STATE.md`: short file updated after every milestone.

---

# Suggested `PROJECT_STATE.md` template

```markdown
# Project State

## Last updated

YYYY-MM-DD

## Current phase

Month 1 / Week 1 — Information Layer Buildout

## Current milestone

Stage 3 complete: SEC + macro + halt + price context.

## Latest verified counts

- filings:
- filing_documents:
- filing_events:
- macro_observations:
- trading_halts:
- prices:
- insider_transactions:

## Known issues

- ...

## Next Codex task

- ...

## Commands to reproduce

```bash
python -m market_info_layer.cli collect-sec
python -m market_info_layer.cli process-sec-filings --form-type 8-K --limit 50
python -m market_info_layer.cli collect-macro
python -m market_info_layer.cli collect-halts
python -m market_info_layer.cli collect-prices --ticker AAPL --period 2y
python -m market_info_layer.cli daily-brief --lookback-days 730 --include-low --output-name stage-review
python -m market_info_layer.cli export-debug --include-db
```
```
