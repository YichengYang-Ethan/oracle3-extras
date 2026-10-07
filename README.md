# oracle3-extras

oracle3-extras extends [oracle3](https://github.com/YichengYang-Ethan/oracle3-prediction-market-agent), the open-source trading engine and MCP server for prediction markets, with integrations and experimental features. It hosts functionality that is too specialized or too new for oracle3 itself, but useful enough that you should not have to write it yourself.

Highlights:

- **Kairos cross-venue pairs as oracle3 relations.** [Kairos](https://kairos.trade) matches the same market on Kalshi, Polymarket, Predict.fun and Hyperliquid. oracle3-extras lines up the outcomes of every pair, checks each one independently (team names and codes, totals lines, game dates, event ids) and writes the `same_event` or `complement` relations oracle3 already understands.
- **Live no-arbitrage scans.** `scan_relations` checks thousands of relations against batched top-of-book quotes, then walks the order books of the survivors to size each edge after fees.
- **MetaMask Agent Wallet execution.** An oracle3 `Trader` that routes Polymarket orders through Agent Wallet, so oracle3 never holds a private key.

oracle3-extras mirrors oracle3's namespaces to make usage and migration as easy as possible:

| oracle3 | oracle3-extras | Adds |
|---|---|---|
| `oracle3.market` (relations) | `oracle3_extras.market` | `align_kalshi_polymarket`, `align_two_outcome_markets`, `market.kairos` |
| `oracle3.arbitrage` (`check_constraint`) | `oracle3_extras.arbitrage` | `scan_relations`, `walk_books` |
| `oracle3.trader` (`Trader`) | `oracle3_extras.trader` | `metamask.AgentWalletTrader` |
| | `oracle3_extras.venues` | Batched, read-only Kalshi and Polymarket data; Predict.fun's fee schedule |

```python
import oracle3_extras as o3x

pairs = await o3x.kairos_relations()                 # Kairos catalog -> oracle3 relations
report = await o3x.scan_relations(pairs.relations)   # live check, sized against the books
for item in report.opportunities():
    print(item.relation.relation_id, item.depth.net_edge)
```

Each integration also has a short path, `oracle3_extras.kairos` and `oracle3_extras.metamask`, that re-exports its public API.

## Installation

```bash
pip install git+https://github.com/YichengYang-Ethan/oracle3-extras.git@v0.3.0
```

Each release is tested against one oracle3 minor series (0.2.x needs oracle3 1.2.x), because oracle3-extras builds on oracle3 internals.

## Integrations

| Integration | Module | Command | Status |
|---|---|---|---|
| [Kairos](https://kairos.trade) | `oracle3_extras.market.kairos` | `oracle3-extras kairos` | Experimental; read-only |
| [MetaMask Agent Wallet](https://docs.metamask.io/agent-wallet) | `oracle3_extras.trader.metamask` | `oracle3-extras metamask` | Experimental; testnet by default |

Every command prints one JSON document on stdout, so agents and scripts can read it; progress goes to stderr (`--quiet` hides it).

## Kairos cross-venue pairs

Kairos is a trading terminal and API that aggregates Kalshi, Polymarket, Predict.fun and Hyperliquid. Its public Data API publishes a catalog of markets it has matched across venues. Finding the same event on two venues is the hardest part of cross-venue research; this integration turns Kairos's catalog into oracle3 relations you can check, store and trade, for every venue pair Kairos lists.

```text
Kairos /matched-markets          oracle3_extras.market.kairos             oracle3
  Kalshi, Polymarket,      -->     look up both markets             -->    same_event / complement relations
  Predict.fun, Hyperliquid         line up outcomes, check, explain        relation store, check_constraint_live,
  similarity >= 0.82                                                       oracle3_extras.arbitrage.scan_relations
```

A daily report built on this integration, by market division (elections, sports, crypto, economics, tech, weather), is published at [Prediction-Illinois/kairos-cross-venue](https://github.com/Prediction-Illinois/kairos-cross-venue).

### Quickstart

```bash
oracle3-extras kairos pairs --rejected     # aligned pairs, and why the others were left out
oracle3-extras kairos sync                 # write the Kalshi–Polymarket pairs into ~/.oracle3/relations.json
oracle3-extras kairos scan                 # cross-venue edges after fees, sized by the books
oracle3-extras scan --source kairos        # re-check the stored relations later
oracle3-extras kairos --venues kalshi,polymarket pairs   # only some venues
```

No account is needed. Kairos's public tier allows 60 catalog requests a minute; walking the whole catalog (11,487 pairs on 7 October 2026) takes 14. Set `KAIROS_CLIENT_ID`, `KAIROS_API_KEY` and `KAIROS_API_SECRET` to use an API key instead (a read-only key is enough; nothing here trades). A key raises Kairos's rate limits tenfold and is needed for the fee quotes that confirm Predict.fun prices.

After `sync`, oracle3's MCP server sees the pairs: `list_relations` lists them, and each relation's markets (`venue`, `market_id`) can be passed straight to `check_constraint_live`. oracle3 prices Kalshi and Polymarket, so `sync` writes only their pairs unless `--venues` names others; the other commands cover every venue.

### How a pair becomes a relation

Market A is the side on the first venue in the order Kalshi, Polymarket, Predict.fun, Hyperliquid. When market A's first outcome (YES on Kalshi) is market B's first outcome, the relation is `same_event` (P(A) = P(B)); when it is the second, `complement` (P(A) + P(B) = 1). Kairos does not say which outcome matches, so oracle3-extras works it out and accepts a pair only when at least one check identifies the outcome and none contradicts it. For pairs with a Kalshi side, `align_kalshi_polymarket`:

| Check | Example |
|---|---|
| Names | Kalshi `Kennesaw St.` is Polymarket outcome `Kennesaw State`, not `Missouri State` |
| Team codes | `KXNHLGAME-26OCT10DALPIT-DAL` is the first team of `nhl-dal-pit-2026-10-10` (`Stars`) |
| Lines | Kalshi `Over 49.5 points` needs a Polymarket `O/U 49.5`; `O/U 50.5` is rejected |
| Periods | A first-half market never matches a full-game one |
| Schedule | The game must start on the day the Kalshi ticker names (one day of slack for time zones), and within three hours of the ticker's start time when it has one; a match Kalshi keeps open after its original date is accepted as postponed, with a warning |

For the other pairs, `align_two_outcome_markets` reads Predict.fun's copies of Polymarket markets (the same question and event; outcomes such as `HOU`/`TEN` are matched as team codes), Hyperliquid's binaries (`League: A v B: Subject`), totals and draws. Two markets with the same question but different events, such as the same "leading at halftime?" question for two different games, are rejected.

On 7 October 2026 at 20:15 UTC, Kairos listed 11,487 pairs and 11,389 were aligned (99.1%):

| Venues | Pairs | Aligned |
|---|---|---|
| Polymarket–Predict.fun | 8,755 | 8,703 |
| Kalshi–Polymarket | 2,296 | 2,253 |
| Kalshi–Predict.fun | 252 | 250 |
| Kalshi–Hyperliquid | 81 | 81 |
| Polymarket–Hyperliquid | 72 | 72 |
| Predict.fun–Hyperliquid | 31 | 30 |

Every pair with a Kalshi or Hyperliquid side was a sports pair; the 1,316 others (crypto, politics, economics, tech and culture) were all Polymarket–Predict.fun. Of the 98 rejected pairs, 52 were Polymarket–Predict.fun markets with the same question about different games, and 39 Kalshi–Polymarket pairs whose dates disagree (11 rain-delayed tennis matches, which 0.4.0 now aligns, and 28 college football games whose Kalshi contracts name another day).

Every relation starts at status `discovered`, with its evidence and warnings in `reasoning`. Read both rulebooks before trading: ties, postponements and cancellations can settle differently on the two venues.

### What a scan reports

`kairos scan` quotes every aligned pair in batches (100 Kalshi markets or 50 Polymarket markets per request), runs oracle3's `check_constraint` with each venue's published fee schedule, and re-checks the pairs with an edge after fees against full order books. Predict.fun has no public book to batch, so its legs are priced from their last trades (Kairos marks) and the 50 largest edges are confirmed with Kairos fee quotes, which walk the live Predict.fun book; without a key they stay unconfirmed and are never reported. Hyperliquid pairs are listed but not priced yet.

On 7 October 2026 at 20:23 UTC, 4 of the 2,253 Kalshi–Polymarket pairs had an edge after fees at the top of the book and 3 survived the order books, the largest worth $0.0075 on 0.74 contracts. 1,156 of the 2,199 pairs priced from Predict.fun last trades showed an edge after fees on those last trades, which are not quotes: without a key, none was confirmed. 6,754 Predict.fun markets had never traded. Cross-venue gaps are rare and small after fees; the scan is built to say so honestly rather than to promise profits.

### History and settlement checks

Kairos drops a pair once its markets expire, so `kairos snapshot` keeps every aligned pair in a local archive; run it a few times a day. Two commands read the archive with Kairos's Market Data API:

```bash
oracle3-extras kairos snapshot --archive pairs.jsonl.gz      # add today's pairs
oracle3-extras kairos history --archive pairs.jsonl.gz       # how far apart the venues traded, by minutes from the start
oracle3-extras kairos settlements --archive pairs.jsonl.gz   # did both venues settle each pair the same way?
```

In Python, `recent_history` compares the venues over the last 24 hours in one-hour buckets instead, up to each event's start, which suits markets that run for months, and `KairosClient.trade_metrics` gives each market's 24-hour volume.

On 7 October 2026, the 253 pairs that had settled on both venues in the previous three days all settled the same way, and a sample of 30 matched Kalshi's and Polymarket's own results. Over the 308 pairs that traded on both venues in the same minute, the median gap between the last trades was 1 cent; the 90th percentile was 2 cents before the start and 6 cents three to four hours in. `history` measures disagreement between trade prices, not arbitrage that could have been executed.

### Attribution and data

Requests carry the User-Agent `oracle3-extras/<version>` and nothing about the user. The integration reads Kairos's public endpoints, keeps no copy of the catalog beyond the relations you choose to save, and never places orders through Kairos. Design notes and our questions for Kairos are in [docs/kairos/INTEGRATION.md](docs/kairos/INTEGRATION.md).

### Relationship with Kairos

This integration uses Kairos's public Data API. It is written and maintained by the oracle3 maintainer and has not been reviewed or endorsed by Kairos. Kairos is a trademark of its owner and is named here only to describe compatibility.

## MetaMask Agent Wallet

[MetaMask Agent Wallet](https://docs.metamask.io/agent-wallet) gives AI agents a self-custodial wallet with security on every transaction: simulation, threat scanning, MEV protection and Guard Mode policies (spend limits, allowlists, 2FA for anything outside them). oracle3 finds and prices trades, and Agent Wallet holds the keys and executes them.

```text
oracle3 strategy or agent             oracle3_extras.trader.metamask         MetaMask Agent Wallet
  research, pricing, fee-aware   -->   AgentWalletTrader               -->   mm predict quote / place / redeem
  no-arbitrage checks                  pre-trade checks, halts                simulation, threat scan, Guard Mode
                                       attribution                            Polymarket CLOB
```

### Why route through Agent Wallet

- **No keys in oracle3.** `oracle3 live run --exchange polymarket` needs a raw private key. This integration needs none: the key stays in Agent Wallet's server wallet (a trusted execution environment) or your own wallet.
- **Policy in front of every order.** Guard Mode spend limits and allowlists apply to every order the strategy sends; anything outside them waits for your approval in MetaMask.
- **Redemption included.** Winning positions are redeemed back to pUSD with one call.

### Setup

```bash
npm install -g @metamask/agent-wallet       # the mm CLI, distributed by MetaMask
mm login                                    # sign in with MetaMask Mobile, Google or email
mm init --wallet server-wallet --mode guard
mm predict mode testnet                     # start on testnet
mm predict setup --wait
oracle3-extras metamask doctor              # checks session, mode, geoblock, balance
```

### Quickstart

```bash
# Price 10 shares against the live book (places nothing)
oracle3-extras metamask quote --token-id <TOKEN_ID> --side buy --size 10

# Place one fill-or-kill order
oracle3-extras metamask place --token-id <TOKEN_ID> --side buy --size 10 --price 0.56 --yes

# Run any oracle3 strategy with orders routed through Agent Wallet
oracle3-extras metamask run \
  --strategy-ref oracle3.strategy.contrib.implication_arb_strategy:ImplicationArbStrategy \
  --duration 3600 --yes

# Positions and redemption
oracle3-extras metamask positions
oracle3-extras metamask redeem --all
```

From Python:

```python
from decimal import Decimal

from oracle3.data.market_data_manager import MarketDataManager
from oracle3.position.position_manager import PositionManager
from oracle3.risk.risk_manager import NoRiskManager
from oracle3.ticker.ticker import PolyMarketTicker
from oracle3.trader.types import TradeSide
from oracle3_extras.metamask import AgentWalletTrader

trader = AgentWalletTrader(MarketDataManager(), NoRiskManager(), PositionManager())
await trader.preflight()          # mode, geoblock and pUSD balance
result = await trader.place_order(
    TradeSide.BUY,
    PolyMarketTicker.from_token_id('<TOKEN_ID>'),
    limit_price=Decimal('0.56'),
    quantity=Decimal('10'),
)
```

`AgentWalletTrader` implements oracle3's `Trader` interface, so existing strategies run unchanged.

### Agent skill

[`skills/oracle3-metamask-agent-wallet`](skills/oracle3-metamask-agent-wallet/SKILL.md) teaches an agent (Claude Code, Codex, Cursor) to research with the oracle3 MCP tools and execute with Agent Wallet, with the user confirming every order. Use it alongside MetaMask's own `metamask-agent-wallet` skill.

### Safety defaults

| Default | Why |
|---|---|
| Testnet unless `allow_mainnet=True` or `--allow-mainnet` | Real funds are an explicit choice |
| Fill-or-kill orders only | A multi-leg trade is never left half-built |
| $25 per-order notional cap | Bounded loss while the integration is new |
| Halt on approval prompts, policy blocks, geoblock, session errors and unreadable order states | Nothing else is sent until a person has looked |
| oracle3 checks first (kill switch, idempotency keys, cash, risk limits), Agent Wallet policy second | Two independent layers |

Polymarket is not available in every region. The integration calls `mm predict geoblock` before trading on mainnet and stops if access is blocked. It does not try to work around regional restrictions.

### Attribution

oracle3 identifies the orders it routes, the way open-source connectors that partner with venues do: [Hummingbot's fee-share partners](https://hummingbot.org/exchanges/bybit/) recognise its traffic by an API header, and [CCXT](https://github.com/ccxt/ccxt) attaches broker IDs. Two things are different here, because users should know what is attached to their orders:

- **What is sent:** the integration ID `oracle3` and the package version, as `MM_INTEGRATION_ID` and `MM_INTEGRATION_VERSION` on each `mm` call, and, once the CLI supports it, as the Polymarket order `metadata` field. MetaMask's own builder code stays in the separate `builder` field.
- **What is not sent:** wallet addresses, balances, strategy names or anything else. Attribution never changes an order's price, size or fees.
- **How to turn it off:** set `ORACLE3_ATTRIBUTION=off`, or pass `Attribution(enabled=False)`.

The design and the open questions are in [docs/metamask/INTEGRATION.md](docs/metamask/INTEGRATION.md).

### Support

- Bugs in this integration: [open an issue here](https://github.com/YichengYang-Ethan/oracle3-extras/issues).
- Agent Wallet, the `mm` CLI, sign-in or wallet policy: [MetaMask Agent Wallet docs](https://docs.metamask.io/agent-wallet) and MetaMask support.

### Relationship with MetaMask

This integration is written and maintained by the oracle3 maintainer, following a conversation with MetaMask's business development team about Agent Wallet integrations. MetaMask has not reviewed this code, and it is not a MetaMask product. MetaMask is a trademark of its owner and is used here only to describe compatibility. The `mm` CLI is distributed separately by MetaMask under its own terms and is not bundled with this package.

## Questions

### What belongs in oracle3-extras?

- Connectors to wallets, data providers and venues that oracle3 itself should not depend on
- Methods that need real-world use before their API settles, such as cross-venue outcome alignment
- Anything that needs an external tool or account (`mm`, a Kairos key) to be useful

Functionality that proves widely useful may graduate into oracle3. Because the namespaces match, graduating changes the import (`oracle3_extras.arbitrage` to `oracle3.arbitrage`), not the code that uses it.

### What does not belong here?

- Code that holds, asks for or logs private keys, seed phrases or exchange passwords
- Strategies or scripts tied to one account or one market
- Case studies and one-off notebooks

### Is the API stable?

Not yet. Until 1.0, minor releases may change the API; every change is listed in the [changelog](CHANGELOG.md).

## Contributing

New integrations are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the layout rules and the checklist every integration follows (tests without network access, safe defaults, a transparent attribution section and a named maintainer).

## License

Apache 2.0; see [LICENSE](LICENSE).

*This software is for research and education. Trading involves financial risk.*
