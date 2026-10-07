# oracle3 × Kairos: integration notes and proposal

Version 0.4.0 · Maintainer: Yicheng Yang (yy85@illinois.edu)

This document describes how `oracle3_extras.market.kairos` uses Kairos's Data API, Market Data API and fee quotes, what we found in the data, and what would make the integration better on both sides. Everything about the APIs below was checked against Kairos's published OpenAPI specifications (`app.kairos.trade/openapi/data-api.yaml`, `market-data-api.yaml`, `execution.yaml`) and live responses on 7 October 2026.

## 1. What the integration does

oracle3 is an open-source trading engine and MCP server for prediction markets: it stores relations between event contracts, prices every leg under the venue's fee schedule, and finds prices that break probability bounds after fees. The hard part of cross-venue work is finding the same event on two venues; Kairos's catalog does exactly that, across Kalshi, Polymarket, Predict.fun and Hyperliquid.

`oracle3_extras.market.kairos` turns the catalog into oracle3 relations:

- `KairosClient.matched_markets()` walks the whole catalog with Kairos's keyset cursor.
- `kairos_relations()` looks up both sides of every pair (Kalshi and Polymarket on their own APIs, Predict.fun and Hyperliquid through Kairos's Market Data API), lines up their outcomes (`oracle3_extras.market.align`) and returns `same_event` or `complement` relations with the evidence, any warnings and Kairos's categories.
- `save_relations()` merges them into oracle3's relation store, which oracle3's MCP tools (`list_relations`, `check_constraint_live`) read.
- `oracle3_extras.arbitrage.scan_relations()` checks them against live prices after fees: Kalshi and Polymarket order books, and Predict.fun last trades (Kairos marks) confirmed with Kairos fee quotes.
- `update_archive()` keeps every aligned pair after Kairos drops it from the catalog. Four checks read the Market Data API: `price_history()` (how far apart the venues traded each pair around its start), `recent_history()` (the same over the last 24 hours, for markets that run for months), `KairosClient.trade_metrics()` (24-hour volume per market) and `check_settlements()` (whether both venues settled the pair the same way).
- `oracle3-extras kairos pairs | sync | scan | snapshot | history | settlements` runs each step from the command line.
- [Prediction-Illinois/kairos-cross-venue](https://github.com/Prediction-Illinois/kairos-cross-venue) runs all of it daily and publishes a report by market division (elections, sports, crypto, economics, tech, weather), with a section of notes for Kairos.

Everything is read-only: no orders go through Kairos or anywhere else.

## 2. Endpoints used

| Call | Use | Notes |
|---|---|---|
| `GET data.kairos.trade/matched-markets?cursor=&limit=1000` | The catalog | Public; documented limit 60 a minute. Cursor mode, `has_more` as the stop signal, restart on 409. A full walk of 11,487 pairs was 14 requests. |
| `GET data.kairos.trade/market-clusters` | `KairosClient.market_clusters()` | Not used by `sync` or `scan`; available for "which venues list this market?" |
| `POST md.kairos.trade/v1/markets/batch` | Predict.fun and Hyperliquid market metadata | 200 ids per call |
| `GET md.kairos.trade/v1/marks` | Predict.fun last trades for the scan | 100 pairs per call (see 4.8) |
| `GET execution.kairos.trade/orders/fee-quote` | Confirm a Predict.fun price on the live book, with fees | Needs a key with `trade:read`; at most 50 relations per scan, a few sizes each |
| `POST md.kairos.trade/v1/candles/batch` | Trade prices for `history` and `recent_history` | Up to 200 series and 9,000 buckets per call, one second apart; stops on repeated failure |
| `GET md.kairos.trade/v1/trades/metrics` | 24-hour volume per market | One call per market; the daily report samples 40 pairs per division |
| `GET md.kairos.trade/v1/resolutions` | Settlement results | 200 markets per call; Kalshi by ticker, the others by numeric market id |
| Kalshi `GET /markets?tickers=`, `GET /series/{ticker}`, `GET /markets/orderbooks` | Verify the Kalshi side, fee schedule, books | 100 tickers per request, 2 requests in flight |
| Polymarket Gamma `GET /markets?id=`, CLOB `POST /books` | Verify the Polymarket side, fee schedule, books | 50 markets per request, 4 in flight |

Requests carry the User-Agent `oracle3-extras/<version> (+https://github.com/YichengYang-Ethan/oracle3-extras)`, so Kairos can see the traffic this integration sends; nothing about the user is sent. An API key is used when `KAIROS_CLIENT_ID`, `KAIROS_API_KEY` and `KAIROS_API_SECRET` are set, and never printed.

## 3. How a pair becomes a relation

Market A is the side whose venue comes first in the order Kalshi, Polymarket, Predict.fun, Hyperliquid. When market A's first outcome (YES on Kalshi) is market B's first outcome, the relation is `same_event`; when it is the second, `complement`. Both are checked natively by `oracle3.arbitrage.check_constraint`, so no outcome is flipped by hand. A pair is aligned only when at least one check identifies the outcome and none contradicts it.

Pairs with a Kalshi side (`align_kalshi_polymarket`):

| Check | Rule |
|---|---|
| Names | A word of the Kalshi YES label names one outcome of market B and not the other (small spelling differences allowed; `St.` = `State`; `ø` = `oe`; a word many team names share, such as `State`, cannot decide alone) |
| Team codes | The code at the end of the Kalshi ticker matches one of the team codes in market B's slug; the opponent's code must not contradict it |
| Lines | Totals need the same line on both venues |
| Periods | Half, quarter, period, set and inning markets match only the same period |
| Schedule | Market B's start must fall on the Kalshi ticker's date (±1 day for time zones) and within 3 hours of the ticker's start time when it has one. A one-day gap is accepted when Kalshi's expected expiration falls a few hours after the start, or, with a warning, when Kalshi's rules are not tied to a date. A longer gap is accepted with a `postponed` warning only when Kalshi's scheduled time passed more than 12 hours ago, its market is still open, and it stays open past the new start (rain-delayed tennis) |

Other pairs (`align_two_outcome_markets`):

| Check | Rule |
|---|---|
| Same question | Predict.fun lists copies of Polymarket markets: the same question and event. Outcomes are then taken in order unless names or codes say they are crossed (`HOU`/`TEN` for Texans/Titans) |
| Same event | Equal event ids count as evidence. Sports event ids that name different dates or teams (`kbo-kt-sam-2026-05-20` and `kbo-kt-sam-2026-08-28`) reject the pair; other differences are a warning |
| Lines and binaries | Totals need the same line; Hyperliquid binaries (`League: A v B: Subject`) are matched on their subject; draws only with draws |

Kalshi–Polymarket relation ids are `kairos:<kalshi ticker>:<polymarket market id>`; others are `kairos:<venue A>:<id A>:<venue B>:<id B>`. Each relation's `confidence` is Kairos's similarity, its status starts at `discovered`, and `valid_until` is the earlier of the two closing times. A later sync refreshes market data but keeps the status and validation results; relations still at `discovered` that left the catalog are removed.

## 4. What we found in the catalog (7 October 2026, 20:15 UTC)

| Venues | Pairs | Aligned |
|---|---|---|
| Polymarket–Predict.fun | 8,755 | 8,703 |
| Kalshi–Polymarket | 2,296 | 2,253 |
| Kalshi–Predict.fun | 252 | 250 |
| Kalshi–Hyperliquid | 81 | 81 |
| Polymarket–Hyperliquid | 72 | 72 |
| Predict.fun–Hyperliquid | 31 | 30 |
| **All** | **11,487** | **11,389 (99.1%)** |

By the categories Kairos gives each side, grouped into the six market divisions of the daily report: Sports 10,171 pairs (Esports included), Crypto 459, Elections & Politics 416, Economics & Finance 276, Tech & Science 141, Climate & Weather none, and 24 Culture.

Observations that may help Kairos:

1. **Matching quality is high.** 99.1% of pairs were confirmed independently by names, team codes, lines, dates or event ids; the rest are below.
2. **Coverage outside sports is Polymarket–Predict.fun only.** Every Kalshi pair (2,629) and every Hyperliquid pair (184) is a sports pair; all 1,316 non-sports pairs are Polymarket–Predict.fun. There are no weather pairs, although Kalshi and Polymarket both list daily weather markets.
3. **Categories differ between the two sides.** 85 pairs carry a different category on each side, 78 of them pointing to different divisions: ITF tennis matches tagged Crypto on Polymarket, Fed-rate markets tagged Finance on Polymarket and Politics on Predict.fun, a college football championship tagged Science. 1,348 pairs have a category on the Kalshi or Predict.fun side only; the Polymarket side has none. One category per pair would make the catalog easier to filter.
4. **Same question, different games.** 52 Polymarket–Predict.fun pairs share the question text but not the game: "Athletic Club leading at halftime?" for 16 September is paired with the same question for 10 October, and "KBO: KT Wiz vs. Samsung Lions" for 20 May with 28 August. The event ids differ (`lal-ray-bil-2026-10-10-halftime-result` vs `lal-lev-bil-2026-09-16-halftime-result`); comparing them would catch these.
5. **Most Predict.fun copies never trade.** Of the 8,983 pairs with a Predict.fun side, 6,754 had no Predict.fun trade at all (no mark).
6. **Only one side of each head-to-head game is matched (Kalshi).** For the Kalshi–Polymarket head-to-head pairs, Kairos matched the Kalshi market of the team Polymarket lists first. The other team's Kalshi market is the exact complement of the same Polymarket market and is missing from the catalog. (Checked on the 0.2.0 catalog; still the case.)
7. **Dates that disagree.** 39 Kalshi–Polymarket pairs were left out because the venues give different days. 11 are tennis matches from the Shanghai Masters with Kalshi tickers dated 6 October and Polymarket starts on 8 October: the same matches, postponed by rain, which 0.4.0 aligns with a warning. 28 are college football games whose Kalshi ticker and rules name another day than Polymarket's start (mostly 16 October for games Polymarket starts on Saturday the 17th); they stay out because those Kalshi contracts are tied to their date.
8. **Three documentation differences.**
   - `/v1/resolutions` resolves Polymarket and Predict.fun markets by numeric market id and returns nothing for condition ids, although the documentation says condition ids.
   - In `/matched-markets`, the Polymarket side's `ticker` is the market's UMA question id (Gamma `questionID`), not its condition id.
   - `/v1/marks` documents 200 pairs per call, but 200 Predict.fun or Polymarket pairs (77-digit token ids) make a URL of about 17,800 characters that the server refuses with HTTP 414; 180 pairs (16,068 characters) went through. `/v1/trades/metrics` returns trades for Polymarket and Predict.fun markets by numeric market id, and no trades for the same markets by condition id or token id; the documentation does not say which id it expects.

## 5. What a scan found (7 October 2026, 20:23 UTC, without a key)

| | Pairs |
|---|---|
| Priced on live Kalshi and Polymarket books | 2,253 |
| · edge after fees at the top of the book | 4 |
| · edge left after walking the order books | 3 (largest $0.0075 on 0.74 contracts) |
| Priced from Predict.fun last trades | 2,199 |
| · edge after fees on those last trades | 1,156 |
| Not priced: Predict.fun market never traded | 6,754 |
| Not priced: Hyperliquid (no fee quotes yet) | 183 |

Cross-venue gaps between Kalshi and Polymarket are rare and small once both fee schedules are applied. The Predict.fun edges come from last trades, not quotes: a mark can be weeks old while the other venue's book has moved. Without a key they stay unconfirmed and are never reported as opportunities; with one, the 50 largest are checked with fee quotes on the live Predict.fun book. The scan assumes every leg fills at the prices used and both markets settle the same way, and it reads the venues one after the other, so prices can move in between.

## 6. What the price history shows (7 October 2026)

`price_history` compares the two venues' last trades in each minute in which both traded, from Kairos one-minute candles. Of the 347 Kalshi–Polymarket pairs whose games started in the 48 hours before 18:03 UTC on 7 October, 308 traded on both venues in the same minute at least once, 9,104 minutes in all.

| Minutes from the start | Minutes compared | Median gap | 90th percentile | Gap of 2¢ or more |
|---|---|---|---|---|
| 120 before to the start | 644 | 1¢ | 2¢ | 27% |
| First 90 minutes | 4,876 | 1¢ | 3¢ | 30% |
| 90 to 180 minutes | 2,954 | 1¢ | 4–5¢ | 40% |
| 180 to 240 minutes | 630 | 2¢ | 6¢ | 52% |

The venues trade close together before a game and drift apart as it goes on, when prices move fastest. Two trades in the same minute can be up to a minute apart, so part of the in-play gap is timing rather than disagreement, and none of it is an executable arbitrage by itself. The daily report repeats this for every venue pair, and compares all divisions over the last 24 hours with hourly candles.

## 7. Proposal, smallest change first

**A. Side mapping in `/matched-markets`.** For each side, the outcome (or token) that corresponds to the other side's YES, as `/market-links` already publishes with `tokenIdYes` and `tokenIdNo`. It would replace inference with Kairos's own answer.

**B. Event check for repeated questions.** Compare event ids or dates before matching two markets whose question text is identical (4.4).

**C. One category per pair.** The same category on both sides, and a category on the Polymarket side (4.3).

**D. More divisions.** Weather markets (Kalshi and Polymarket both list them daily), and Kalshi pairs outside sports.

**E. Both teams of head-to-head games.** Pair the second team's Kalshi market with the same Polymarket market (a complement), roughly doubling the usable Kalshi head-to-head pairs.

**F. Marks by POST, or a matching limit.** A `POST /v1/marks` body, or a documented cap that fits in a URL with 77-digit token ids (4.8).

**G. A research API key.** Higher limits for the daily report, and stream access to the market-data WebSocket so scans can read live books from one source instead of polling venues.

**H. Catalog history.** Snapshots by `catalog_version`, so cross-venue spreads can be studied over time.

**I. Listing.** A link to oracle3-extras or the daily report from the Kairos API docs, as open-source examples built on the Data API.

## 8. Open questions for the Kairos team

1. **Data terms.** The API specification links `kairos.trade/terms`, which returned 404 on 7 October 2026. Which terms apply to the public APIs, and may users keep matched pairs in a local relation store? The daily report publishes only aggregates until we know.
2. **Attribution.** Is the User-Agent enough for Kairos to see this traffic, or would you prefer a header or parameter of your own?
3. **Load.** Is the daily report's traffic (one catalog walk, about 120 candle batches, up to 500 trade-metric calls, up to 250 fee quotes) acceptable, and at what hour would you prefer it?
4. **Hyperliquid.** `/orders/fee-quote` covers Kalshi, Polymarket and Predict.fun. Are Hyperliquid quotes planned? Until then its pairs are listed but not priced.
5. **Trade metrics ids.** Which `contract_id` does `/v1/trades/metrics` expect for Hyperliquid markets? For Polymarket and Predict.fun the numeric market id works.

## 9. Milestones

| | Scope | Status |
|---|---|---|
| M1 | Client, outcome alignment, relations, scan, CLI; offline tests and a live contract test | Done (0.2.0) |
| M2 | Record cross-venue prices and settlements over time; a public daily report | Done (0.3.0, 0.4.0, Prediction-Illinois/kairos-cross-venue) |
| M3 | Every Kairos venue and market division | Done (0.4.0); Hyperliquid prices wait for fee quotes |
| M4 | Live books from the Kairos market-data WebSocket instead of venue polling | Needs a key with stream access |
| M5 | Outcome alignment and relation scans graduate into oracle3 | After M4 |

Execution through Kairos is not planned: oracle3 executes through venue APIs and MetaMask Agent Wallet.

## 10. Maintenance commitments

- Run the live contract test (`pytest --live`) before every release and after Kairos changes an API.
- One named maintainer and a security contact for the integration.
- Integration bugs are handled in this repository; API issues go to Kairos.
