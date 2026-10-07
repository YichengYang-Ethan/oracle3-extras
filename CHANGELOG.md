# Changelog

## [0.1.0] - Unreleased

### Added

- `oracle3_extras.metamask`: Polymarket execution through MetaMask Agent Wallet.
  - `AgentWalletClient`, a typed wrapper around the `mm` CLI's JSON envelope (quote, place, cancel, orders, positions, balance, redeem, mode, geoblock, doctor).
  - `AgentWalletTrader`, an oracle3 `Trader` that needs no private key: testnet by default, fill-or-kill only, a per-order notional cap, and a halt on approval prompts, policy blocks, geoblock, session errors or unreadable order states.
  - `run_live_agent_wallet_trading` and `oracle3-extras metamask run` to run any oracle3 strategy with Agent Wallet execution.
  - Transparent, opt-out attribution (`ORACLE3_ATTRIBUTION=off`).
- Agent skill `oracle3-metamask-agent-wallet`.
- `oracle3-extras` command-line entry point.
