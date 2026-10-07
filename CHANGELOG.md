# Changelog

## [0.2.0] - Unreleased

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
