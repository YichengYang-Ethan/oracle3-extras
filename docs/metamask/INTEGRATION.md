# oracle3 × MetaMask Agent Wallet: integration notes and proposal

Version 0.2.0 · Maintainer: Yicheng Yang (yy85@illinois.edu)

This document describes how `oracle3_extras.trader.metamask` (also importable as `oracle3_extras.metamask`) uses MetaMask Agent Wallet, how it attributes the orders it routes, and what would make the integration better on both sides. Everything about the `mm` CLI below was checked against the published `@metamask/agent-wallet` 7.0.0 package and the Agent Wallet documentation.

## 1. What the integration does

oracle3 is an open-source trading engine and MCP server for prediction markets: it maps relations between event contracts, prices every leg under the venue's fee schedule, and finds prices that break probability bounds after fees. Until now, live Polymarket execution in oracle3 required a raw private key.

`oracle3_extras.trader.metamask` replaces that key with Agent Wallet:

- `AgentWalletTrader` implements oracle3's `Trader` interface, so every existing oracle3 strategy can execute through Agent Wallet without changes.
- `AgentWalletClient` wraps the `mm` CLI's JSON envelope with typed results and errors.
- `oracle3-extras metamask run --strategy-ref ...` runs a strategy end to end.
- An agent skill lets Claude Code, Codex or Cursor research with oracle3's MCP tools and execute with Agent Wallet, with the user confirming each order.

## 2. Command mapping

| oracle3 operation | Agent Wallet command | Notes |
|---|---|---|
| Session check | `mm doctor` | `authenticated` and `initialized` must be true |
| Mode check | `mm predict mode [testnet\|mainnet]` | The trader refuses mainnet unless the caller opts in |
| Region check | `mm predict geoblock` | Checked before mainnet trading; a block stops the trader |
| Cash sync | `mm predict balance --sync` | pUSD in the deposit wallet (6-decimal base units) |
| Pre-trade price | `mm predict quote` | Walks the book; `worstPrice` becomes the FOK limit |
| Execution | `mm predict place --order-type FOK` | Fill price = making / taking amount, fees included |
| Position sync | `mm predict positions` | Loaded into oracle3's position manager |
| Settlement | `mm predict redeem --all --wait` | Winnings back to pUSD |

## 3. Safety model

Two independent layers sit in front of every order:

1. **oracle3:** kill switch, idempotency keys, ticker allowlist, cash check, a per-order notional cap ($25 by default) and the strategy's risk manager.
2. **Agent Wallet:** transaction simulation, threat scanning, MEV protection and Guard Mode policy (spend limits, allowlists, 2FA for anything outside them).

The trader stops sending orders, and does not retry, after an approval or 2FA prompt, a policy block, a geoblock, a session or predict-setup error, a CLI timeout, or an order status it cannot read (`delayed`, `live` or empty). It uses fill-or-kill orders only, so a basket is never left half-built by this layer.

## 4. Attribution

### What exists today

From the published CLI and SDK:

- `mm predict place` attaches the **builder code returned by MetaMask's fee service** to the CLOB V2 `builder` field of each order, which attributes the order flow to MetaMask.
- CLI analytics record which **agent host** launched the CLI (`MM_PLUGIN_HOST`: Claude Code, Cursor, Codex, Antigravity, Grok) and campaign data. There is no slot for a third-party integration such as oracle3.
- The SDK's order input already accepts a **`metadata`** field (bytes32), which the CLOB V2 order signs alongside `builder`. `mm predict place` does not expose it yet.

### What this integration sends

- `MM_INTEGRATION_ID=oracle3` and `MM_INTEGRATION_VERSION=<version>` in the environment of every `mm` call. Today the CLI ignores them.
- `--metadata 0x6f7261636c6533…00` (ASCII `oracle3`, right-padded to 32 bytes) on `mm predict place`, but only when the installed CLI lists that flag. Today it is not sent.

Nothing else is sent: no addresses, balances or strategy names. Attribution never changes an order's price, size or fees, and users can turn it off with `ORACLE3_ATTRIBUTION=off`.

### Proposal, smallest change first

**A. Expose `--metadata` on `mm predict place`.** It is one flag; the SDK already supports the field. MetaMask's builder code stays in `builder`, so MetaMask keeps its fee attribution, and the integration ID sits in `metadata`, where anyone can verify it on-chain. A referral report is then MetaMask's builder-attributed trades (Polymarket's `/builder/trades` already lists them) grouped by `metadata`.

**B. Record an integration ID next to `MM_PLUGIN_HOST` in CLI analytics.** Either read `MM_INTEGRATION_ID`, or add an `--integration` flag. Issuing IDs per partner would stop one integration from claiming another's traffic.

**C. A periodic report per integration ID** (orders, volume, active users, fees) as the basis for the referral program discussed on our call.

### Principles

Open-source connectors that share fees with venues show what works and what does not. Hummingbot's exchange partners recognise its traffic by an API header and share fees at no cost to users. CCXT's preset broker IDs drew criticism because users did not know they were there. This integration therefore documents attribution in its README, makes it opt-out, keeps it free of personal data, and never lets it change an order.

## 5. Open questions for the MetaMask team

1. **Fees on the agent route.** The in-app predict feature lists a 4% fee per trade plus Polymarket's taker fee. Does the same apply to `mm predict place`? oracle3's arbitrage strategies work with edges of a few cents per contract, so this decides which strategies the integration should offer.
2. **Testnet coverage.** Does testnet mode support the whole flow (setup, deposit, quote, place, positions, redeem) for developers in regions where Polymarket mainnet is unavailable?
3. **Order states.** For fill-or-kill orders that come back `delayed`, what is the recommended way to learn the final state? The trader currently halts and asks a person to check `mm predict orders`.
4. **Policy on predict orders.** Do Guard Mode outflow limits apply to orders placed from the predict deposit wallet, or only to deposits into it?
5. **Listing.** Could the oracle3 skill be listed next to MetaMask's `agent-skills`, and could MetaMask review the integration?

## 6. Milestones

| | Scope | Status |
|---|---|---|
| M1 | Client, trader, CLI, agent skill; 46 tests against a scripted `mm` | Done |
| M2 | End-to-end testnet run with a real Agent Wallet session; PyPI release | Next (repository is public) |
| M3 | Attribution live once A or B ships; a jointly written integration guide | Depends on MetaMask |
| M4 | A read-only `mm` plugin exposing oracle3's analytics inside the CLI (plugins cannot reach the predict service, so execution stays in the skill and trader) | Planned |
| M5 | Usage review and referral or sponsorship terms | After M2–M3 |

## 7. Maintenance commitments

- Track each `mm` minor release and keep the test fixtures in step with the published JSON envelope.
- One named maintainer and a security contact for the integration.
- Integration bugs are handled in this repository; Agent Wallet and CLI issues go to MetaMask.
