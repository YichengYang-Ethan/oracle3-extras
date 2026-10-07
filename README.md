# oracle3-extras

Optional and experimental integrations for [oracle3](https://github.com/YichengYang-Ethan/oracle3-prediction-market-agent), the open-source trading engine and MCP server for prediction markets.

oracle3 core stays small and venue-neutral. Integrations with wallets, venues and partner platforms live here, are versioned independently, and move into core once they are stable, the way [pymc-extras](https://github.com/pymc-devs/pymc-extras) relates to PyMC.

```bash
pip install oracle3-extras
```

## Integrations

| Integration | Module | What it adds | Status |
|---|---|---|---|
| [MetaMask Agent Wallet](https://docs.metamask.io/agent-wallet) | `oracle3_extras.metamask` | Polymarket execution, custody and redemption through Agent Wallet, so oracle3 never holds a private key | Experimental; testnet by default |

---

## MetaMask Agent Wallet

[MetaMask Agent Wallet](https://docs.metamask.io/agent-wallet) gives AI agents a self-custodial wallet with security on every transaction: simulation, threat scanning, MEV protection and Guard Mode policies (spend limits, allowlists, 2FA for anything outside them). oracle3 finds and prices trades, and Agent Wallet holds the keys and executes them.

```text
oracle3 strategy or agent             oracle3_extras.metamask                MetaMask Agent Wallet
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
pip install oracle3-extras
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

## Contributing

New integrations are welcome. Each one is a subpackage under `oracle3_extras/` with its own tests, a section in this README, a named maintainer and safe defaults (paper or testnet first). See the [oracle3 contributing guide](https://github.com/YichengYang-Ethan/oracle3-prediction-market-agent/blob/main/.github/CONTRIBUTING.md).

## License

Apache 2.0; see [LICENSE](LICENSE).

*This software is for research and education. Trading involves financial risk.*
