# oracle3 × Kairos: integration notes and proposal

Version 0.2.0 · Maintainer: Yicheng Yang (yy85@illinois.edu)

This document describes how `oracle3_extras.market.kairos` uses Kairos's public Data API, what we found in the matched-market catalog, and what would make the integration better on both sides. Everything about the API below was checked against Kairos's published OpenAPI specification (`app.kairos.trade/openapi/data-api.yaml`) and live responses on 7 October 2026.

## 1. What the integration does

oracle3 is an open-source trading engine and MCP server for prediction markets: it stores relations between event contracts, prices every leg under the venue's fee schedule, and finds prices that break probability bounds after fees. The hard part of cross-venue work is finding the same event on two venues; Kairos's catalog does exactly that.

`oracle3_extras.market.kairos` turns the catalog into oracle3 relations:

- `KairosClient.matched_markets()` walks the whole catalog with Kairos's keyset cursor.
- `kairos_relations()` looks up both sides of every Kalshi–Polymarket pair on the venues, lines up their outcomes (`oracle3_extras.market.align`) and returns `same_event` or `complement` relations, with the evidence and any warnings.
- `save_relations()` merges them into oracle3's relation store, which oracle3's MCP tools (`list_relations`, `check_constraint_live`) read.
- `oracle3_extras.arbitrage.scan_relations()` checks them against live prices after fees and sizes the survivors against the order books.
- `oracle3-extras kairos pairs | sync | scan` runs each step from the command line.

Everything is read-only: no orders go through Kairos or anywhere else.

## 2. Endpoints used

| Call | Use | Notes |
|---|---|---|
| `GET data.kairos.trade/matched-markets?cursor=&provider=kalshi&limit=1000` | The catalog | Public; documented limit 60 a minute. Cursor mode, `has_more` as the stop signal, restart on 409. A full walk is 4 requests. |
| `GET data.kairos.trade/market-clusters` | `KairosClient.market_clusters()` | Not used by `sync` or `scan`; available for "which venues list this market?" |
| Kalshi `GET /markets?tickers=`, `GET /series/{ticker}`, `GET /markets/orderbooks` | Verify the Kalshi side, fee schedule, books | 100 tickers per request, 2 requests in flight |
| Polymarket Gamma `GET /markets?id=`, CLOB `POST /books` | Verify the Polymarket side, fee schedule, books | 50 markets per request |

Requests carry the User-Agent `oracle3-extras/<version> (+https://github.com/YichengYang-Ethan/oracle3-extras)`, so Kairos can see the traffic this integration sends; nothing about the user is sent. An API key is used when `KAIROS_CLIENT_ID`, `KAIROS_API_KEY` and `KAIROS_API_SECRET` are set, and never printed.

## 3. How a pair becomes a relation

Kalshi is always market A. When Kalshi YES is Polymarket's first outcome the relation is `same_event`; when it is the second, `complement`. Both are checked natively by `oracle3.arbitrage.check_constraint`, so no outcome is flipped by hand.

A pair is aligned only when at least one check identifies the outcome and none contradicts it:

| Check | Rule |
|---|---|
| Names | A word of the Kalshi YES label names one Polymarket outcome and not the other (small spelling differences allowed; `St.` = `State`; `ø` = `oe`) |
| Team codes | The code at the end of the Kalshi ticker matches one of the team codes in the Polymarket slug; the opponent's code must not contradict it |
| Lines | Totals need the same line on both venues |
| Periods | Half, quarter, period, set and inning markets match only the same period |
| Schedule | Polymarket's `gameStartTime` must fall on the Kalshi ticker's date (±1 day for time zones) and within 3 hours of the ticker's start time when it has one. A one-day gap is accepted when Kalshi's expected expiration falls a few hours after the start, or, with a warning, when Kalshi's rules are not tied to a date (tennis) |

Each relation's id is `kairos:<kalshi ticker>:<polymarket market id>`, its `confidence` is Kairos's similarity, its status starts at `discovered`, and `valid_until` is the earlier of the two closing times. A later sync refreshes market data but keeps the status and validation results; relations still at `discovered` that left the catalog are removed.

## 4. What we found in the catalog (7 October 2026)

| | Pairs |
|---|---|
| Catalog pairs with a Kalshi side | 2,793 |
| Kalshi–Polymarket | 2,461 |
| Kalshi–Predict.fun / Hyperliquid–Kalshi (not used: oracle3 does not trade these venues) | 251 / 81 |
| Aligned | **2,432 (98.8%)** |
| · totals with the same line | 1,170 |
| · head-to-head (team or player names) | 666 |
| · single-team Yes/No markets | 396 |
| · draws | 200 |
| Aligned with a warning (postponed or rescheduled game) | 46 |
| Rejected: dates disagree | 25 |
| Rejected: Kalshi names a city, Polymarket a club, codes differ (`Bilbao` / `Athletic Club`) | 4 |

Observations that may help Kairos:

1. **Matching quality is high.** Where both the name check and the team-code check applied (815 pairs), they never disagreed, and no pair matched a different line or a different team.
2. **Only one side of each head-to-head game is matched.** For all 666 aligned head-to-head pairs, Kairos matched the Kalshi market of the team Polymarket lists first. The other team's Kalshi market is the exact complement of the same Polymarket market, so those pairs are just as tradeable and are currently missing from the catalog.
3. **Date conflicts are on the venues' side.** 23 of the 25 rejected pairs are college football games whose Kalshi rules name 16 October while Polymarket starts them on 17 October (US Eastern time). The pairs are probably right; we leave them out because the Kalshi contract is tied to a date.
4. **Side mapping is not available yet.** `/matched-markets/enriched`, which would give outcomes and token ids for both sides, returned `503 Market enrichment unavailable` at 05:40 and 06:10 UTC, and `/market-links/featured`, which publishes per-venue YES/NO tokens, returned no links. With either, the integration could use Kairos's own side mapping instead of inferring it.

## 5. What a scan found (7 October 2026, 06:09 UTC)

| | Pairs |
|---|---|
| Quoted | 2,432 |
| Break the no-arbitrage bound before fees (top of book) | 45 |
| Edge after fees at the top of the book | 5 |
| Edge left after walking the order books | 2 ($0.014 on 5 contracts; $0.0004 on 0.05 contracts) |

Cross-venue gaps between Kalshi and Polymarket are rare and small once both fee schedules are applied; most top-of-book edges disappear in the order books. The scan is designed to report that honestly. It assumes every leg fills at the prices used and both markets settle the same way, and it reads the two venues one after the other, so prices can move in between.

## 6. Proposal, smallest change first

**A. Side mapping in `/matched-markets`.** For each side, the outcome (or token) that corresponds to the other side's YES, as `/market-links` already publishes with `tokenIdYes` and `tokenIdNo`. It would replace inference with Kairos's own answer and cover non-sports pairs, which this integration does not align today.

**B. Both teams of head-to-head games.** Pair the second team's Kalshi market with the same Polymarket market (a complement), roughly doubling the usable head-to-head pairs.

**C. A research API key.** Higher limits for scheduled scans, and stream access to the market-data WebSocket so that scans can read live and synthetic books from one source instead of polling two venues.

**D. Catalog history.** Snapshots by `catalog_version`, so that cross-venue spreads can be studied over time.

**E. Listing.** A link to oracle3-extras from the Kairos API quickstart or docs, as an open-source example built on the Data API.

## 7. Open questions for the Kairos team

1. **Data terms.** The API specification links `kairos.trade/terms`, which returned 404 on 7 October 2026. Which terms apply to the public Data API, and may users keep matched pairs in a local relation store?
2. **Attribution.** Is the User-Agent enough for Kairos to see this traffic, or would you prefer a header or parameter of your own?
3. **Enrichment.** Is the 503 from `/matched-markets/enriched` expected for anonymous callers?

## 8. Milestones

| | Scope | Status |
|---|---|---|
| M1 | Client, outcome alignment, relations, scan, CLI; offline tests and a live contract test | Done (0.2.0) |
| M2 | Scheduled scans that record cross-venue spreads over time | Next |
| M3 | Live books from the Kairos market-data WebSocket instead of venue polling | Needs a key with stream access |
| M4 | Outcome alignment and relation scans graduate into oracle3 | After M2 |

Execution through Kairos is not planned: oracle3 executes through venue APIs and MetaMask Agent Wallet.

## 9. Maintenance commitments

- Run the live contract test (`pytest --live`) before every release and after Kairos changes the Data API.
- One named maintainer and a security contact for the integration.
- Integration bugs are handled in this repository; Data API issues go to Kairos.
