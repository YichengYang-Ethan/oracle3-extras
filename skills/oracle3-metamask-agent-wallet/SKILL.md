---
name: oracle3-metamask-agent-wallet
description: Research Polymarket trades with the oracle3 MCP tools, then execute them through MetaMask Agent Wallet (the mm CLI) with the user confirming every order. Testnet unless the user asks for mainnet.
---

# oracle3 with MetaMask Agent Wallet

Use this skill when the user wants to act on an oracle3 finding (a mispriced market, a fair-value gap, a no-arbitrage violation) with real or testnet orders, without giving oracle3 a private key. oracle3 does the research and pricing; MetaMask Agent Wallet holds the keys and executes. Use it together with MetaMask's own `metamask-agent-wallet` skill, which covers sign-in and wallet setup.

## Tools

- oracle3 MCP server (`uvx oracle3 mcp`): `search_markets`, `get_market`, `get_quote`, `get_orderbook`, `check_constraint_live`, `fair_value`, `trading_fee`. All read-only.
- `oracle3-extras metamask ...` (`pip install git+https://github.com/YichengYang-Ethan/oracle3-extras.git`): `doctor`, `mode`, `quote`, `place`, `positions`, `redeem`. Every command prints JSON.
- The `mm` CLI underneath (`npm install -g @metamask/agent-wallet`).

## Before the first order

1. Run `oracle3-extras metamask doctor`. `doctor.authenticated` and `doctor.initialized` must both be true. If not, ask the user to run `mm login` and `mm init` themselves.
2. Use testnet (`oracle3-extras metamask mode testnet`) unless the user has asked for mainnet in this conversation.
3. On mainnet, `doctor` reports `geoblocked`. If it is true, stop and tell the user Polymarket is unavailable from their location.

## Research with oracle3

1. Find markets with `search_markets` and read the resolution rules with `get_market`.
2. Price the idea: `fair_value` for a single market, `check_constraint_live` for related markets.
3. Check costs with `get_quote` and `trading_fee`. An edge only counts after fees on every leg.

## Execute with Agent Wallet

1. Quote each leg: `oracle3-extras metamask quote --token-id <id> --side buy --size <shares>`. Use `worst_price` and `cost`, not the midpoint.
2. Show the user the market, side, size, worst price, total cost and the edge after fees. Wait for an explicit yes.
3. Place a fill-or-kill order at the quoted worst price: `oracle3-extras metamask place --token-id <id> --side buy --size <shares> --price <worst_price> --yes`.
4. If `matched` is false, report it. Do not retry at a worse price unless the user says so.
5. For a multi-leg basket, quote every leg first and place nothing unless every leg is fully filled at the quoted price. If a leg fails after another has filled, stop and report the open position.

## After the trade

- `oracle3-extras metamask positions` shows open positions.
- Once markets resolve, `oracle3-extras metamask redeem --all` returns winnings to pUSD.

## Hard rules

- Never switch to mainnet or Beast Mode on your own.
- Never raise spend limits or edit allowlists; those are the user's decisions in MetaMask.
- If a command reports an approval or 2FA prompt, stop and ask the user to approve it in MetaMask. Do not send the order again.
- Never handle private keys, seed phrases, passwords or CLI tokens.
- Orders carry the integration ID `oracle3` for attribution. Mention this once if the user asks what is sent; `ORACLE3_ATTRIBUTION=off` turns it off.
