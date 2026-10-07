# Changelog

## [0.4.0] - 2026-10-07

### Added

- Every venue Kairos matches: Kalshi, Polymarket, Predict.fun and Hyperliquid. `kairos_relations(venues=...)` and the `--venues` option pick any two or more; by default all four. `kairos sync` still writes only Kalshi–Polymarket pairs unless `--venues` says otherwise, because oracle3's own tools price those two venues.
  - `align_two_outcome_markets` lines up pairs without a Kalshi side: Predict.fun's copies of Polymarket markets (same question and event, outcomes sometimes given as team codes such as `HOU`/`TEN`), Hyperliquid HIP-4 binaries (`League: A v B: Subject`), totals and draws.
  - `as_polymarket_shape` and `expand_labels` turn Kairos Market Data metadata into the Gamma shape the aligners read; `build_relation` and `relation_id` build relations for any venue pair (Kalshi–Polymarket ids are unchanged).
  - Relations keep Kairos's categories (`analysis_b['kairos_categories']`).
- `scan_relations` prices Predict.fun legs from their last trades (Kairos marks) and confirms the largest edges with Kairos fee quotes, which walk the live Predict.fun book (`max_confirm`, 50 by default; `kairos scan --max-confirm`). Fee quotes need a Kairos key with the `trade:read` scope; without one these edges stay unconfirmed and are never reported as opportunities. Hyperliquid pairs are skipped: Kairos has no fee quotes for it yet.
- `KairosClient.markets`, `marks`, `fee_quote` and `trade_metrics` (24-hour volume per market, `/v1/trades/metrics`).
- `recent_history`: both venues' trade prices over the last 24 hours in one-hour buckets, up to each event's start, for markets that run for months.
- `price_history`, `check_settlements` and the archive work for every venue pair.
- `venues.PredictFunSchedule`: Predict.fun's taker fee, `rate × min(p, 1 − p)` per share.

### Changed

- `KairosClient.candles` sends up to 200 series per call as long as they add up to at most 9,000 buckets (`max_bars`), instead of 25 series per call: one-minute windows still go about 25 at a time, hourly ones 200.
- `/v1/marks` calls carry at most 100 pairs and 15,000 characters: 200 pairs with 77-digit token ids made a URL Kairos's server refused (HTTP 414).
- HTTP requests are retried four times instead of three.
- Renamed, because pairs no longer always have a Kalshi and a Polymarket side:
  - `SettlementCheck.kalshi` and `.polymarket` are now `.first` and `.second`; the old names still work and warn until the next minor release.
  - Dictionary keys: `kalshi` and `polymarket` are now `market_a` and `market_b` in `PairHistory.summary()` and in `kairos pairs` rows; `SettlementCheck.to_dict()` has `market_a`, `market_b`, `a_first_outcome_paid` and `b_first_outcome_paid` instead of `kalshi_market`, `polymarket_market`, `kalshi_yes_paid` and `polymarket_first_outcome_paid`. Both now also have `venues`.
- `KairosRelations.summary()` adds `considered` and `aligned_by_venues`; `history_summary` counts pairs without a start time in its overall statistics.

### Fixed

- One venue failing no longer stops a scan or a settlement check: its relations are skipped as `<venue> data unavailable` and the rest go on. A DNS error at Polymarket had ended a 27-minute scan with nothing to show.
- `gather_limited` cancels and closes the jobs still queued when one fails, so none is left running or unawaited.
- Predict.fun markets that have never traded are reported as `no Predict.fun trades yet` instead of being priced with no prices.
- Tennis matches postponed by rain are lined up with a `postponed` warning instead of rejected as `different dates`: Kalshi keeps the original date in the ticker and the market open until the match is played, while Polymarket lists the new start.
- A word many team names share (`state`, `university`, `college`) no longer decides which Kalshi team an outcome is: Jacksonville St. had been lined up with Kennesaw State's code.

## [0.3.0] - 2026-10-07

### Added

- `KairosClient.candles` and `KairosClient.resolutions`: candles and settlement results from Kairos's Market Data API (`md.kairos.trade`). Candles go 25 series per call with a pause between calls, and the client stops instead of retrying when Kairos keeps failing.
- `oracle3_extras.market.archive`: a rolling archive of relations (`update_archive`, `load_archive`, `started_between`), so pairs outlive the Kairos catalog, which drops them once their markets expire.
- `price_history` and `history_summary`: Kalshi and Polymarket trade prices in the minutes both venues traded, around each event's start.
- `check_settlements` and `settlement_summary`: whether both venues settled each pair the same way.
- Commands `kairos snapshot`, `kairos history` and `kairos settlements`.
- Relations record Polymarket's `game_start` and Kalshi's `expected_expiration`.
- `kairos_relations(catalog=...)` aligns a saved catalog; `venues.polymarket_markets(..., include_closed=True)` also finds closed markets.
- A live contract test checks Kairos's settlement results against Kalshi's and Polymarket's own.

### Fixed

- For Polymarket, `KairosMarket.ticker` is the market's UMA question id, not its condition id (documentation).

## [0.2.0] - 2026-10-07

### Added

- `oracle3_extras.market.kairos`: Kairos's cross-venue matched-market catalog as oracle3 relations.
  - `KairosClient`, a read-only client for the public Kairos Data API (cursor walk with restart on catalog changes, optional API key).
  - `kairos_relations` aligns every Kalshi–Polymarket pair into a `same_event` or `complement` relation with its evidence and warnings, and explains the pairs it leaves out.
  - `save_relations` merges them into oracle3's relation store in one write, keeping each relation's status and validation.
- `oracle3_extras.market.align.align_kalshi_polymarket`: line up a Kalshi market's YES outcome with a Polymarket outcome by name, team code, line, period and schedule.
- `oracle3_extras.arbitrage.scan_relations` and `walk_books`: check many relations against batched live quotes after fees, then size the survivors against the order books.
- `oracle3_extras.venues`: batched, read-only Kalshi and Polymarket market data, fee schedules and order books.
- Commands `oracle3-extras kairos pairs | sync | scan` and `oracle3-extras scan`, and a global `--quiet`.
- Agent skill `oracle3-kairos-cross-venue`.
- `pytest --live` contract tests against the real APIs; doctests run with the suite.
- Release workflow (`.github/workflows/pypi-publish.yml`): every push builds the sdist and the wheel and checks that both install and import; publishing a GitHub release uploads them to PyPI with Trusted Publishing. Setup in `.github/PYPI-SETUP.md`.
- pre-commit hooks (ruff, formatting, YAML/TOML and whitespace checks), also run in CI.
- CONTRIBUTING: how a feature graduates into oracle3 and how public names are renamed or removed.

### Changed

- The package mirrors oracle3's namespaces, like pymc-extras mirrors PyMC: the MetaMask integration moved to `oracle3_extras.trader.metamask`. `oracle3_extras.metamask` still works and re-exports the same objects.
- `import oracle3_extras as o3x` exposes the main entry points; the version is read from the installed package.
- Requires oracle3 1.2.x (`oracle3>=1.2.2,<1.3`) and declares `httpx` directly.
- New `dev` extra (tests and ruff).
- Warnings now fail the test suite.

## [0.1.0] - 2026-10-06

### Added

- `oracle3_extras.metamask`: Polymarket execution through MetaMask Agent Wallet.
  - `AgentWalletClient`, a typed wrapper around the `mm` CLI's JSON envelope (quote, place, cancel, orders, positions, balance, redeem, mode, geoblock, doctor).
  - `AgentWalletTrader`, an oracle3 `Trader` that needs no private key: testnet by default, fill-or-kill only, a per-order notional cap, and a halt on approval prompts, policy blocks, geoblock, session errors or unreadable order states.
  - `run_live_agent_wallet_trading` and `oracle3-extras metamask run` to run any oracle3 strategy with Agent Wallet execution.
  - Transparent, opt-out attribution (`ORACLE3_ATTRIBUTION=off`).
- Agent skill `oracle3-metamask-agent-wallet`.
- `oracle3-extras` command-line entry point.
