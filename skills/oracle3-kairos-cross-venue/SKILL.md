---
name: oracle3-kairos-cross-venue
description: Find the same market on Kalshi, Polymarket, Predict.fun and Hyperliquid with Kairos's matched-market catalog, line up the outcomes, and check the pairs for an edge after fees with oracle3. Read-only; places no orders.
---

# Cross-venue research with Kairos and oracle3

Use this skill when the user wants to compare prices for the same event on two venues (Kalshi, Polymarket, Predict.fun or Hyperliquid), look for cross-venue arbitrage, or load cross-venue pairs into oracle3. Kairos matches the markets; oracle3-extras lines up their outcomes and checks prices after both venues' fees. Nothing here places an order.

## Tools

- `oracle3-extras kairos pairs | sync | scan | snapshot | history | settlements` and `oracle3-extras scan` (`pip install git+https://github.com/YichengYang-Ethan/oracle3-extras.git`). Every command prints JSON. `oracle3-extras kairos --venues kalshi,polymarket pairs` limits the catalog to some venues; the default is all four.
- The oracle3 MCP server (`uvx oracle3 mcp`): `get_market` for resolution rules, `check_constraint_live` to re-check one Kalshi–Polymarket pair, `list_relations` for stored pairs.

## Steps

1. Run `oracle3-extras --quiet kairos scan --top 10`. Read `summary` and `opportunities`.
2. For each opportunity, read `basket`, `depth.contracts`, `depth.net_edge` and `warnings`. The `depth` figures (walked down the order books, or Kairos fee quotes for Predict.fun) count; `top_of_book` alone does not.
3. Predict.fun legs are priced from last trades, which can be weeks old. Without a Kairos API key they stay `unconfirmed` and are never opportunities; say so rather than reporting their top-of-book edges. Hyperliquid pairs are listed but not priced.
4. Before calling anything an arbitrage, read both rulebooks (`get_market` for Kalshi and Polymarket; the venue's own page otherwise). Ties, postponements and cancellations can settle differently on the two venues.
5. To keep the pairs for later, run `oracle3-extras --quiet kairos sync`, then `list_relations` and `check_constraint_live` work on them.
6. For questions about the past, keep an archive (`oracle3-extras --quiet kairos snapshot --archive pairs.jsonl.gz`, a few times a day) and read it with `kairos history` (how far apart the venues traded around each start) or `kairos settlements` (whether both venues settled each pair the same way). History compares trade prices, not quotes: never present a historical gap as an arbitrage that could have been taken.

## How to report

- State the net edge after fees and the size it lasts for, with the time of the scan and the two venues.
- Say how many pairs were checked and how many had an edge. Edges are usually rare and small; say so when the scan finds none.
- Mention every warning (postponed or rescheduled games) and any rule difference you found.

## Hard rules

- Do not place orders from this skill. If the user wants to trade, confirm the venue, side, size and price with them first and use their own tools.
- Do not describe a relation as risk-free until both rulebooks have been read.
- Never ask for or handle API keys, passwords or private keys. Kairos keys, if any, are set by the user as environment variables.
