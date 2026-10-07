# Contributing to oracle3-extras

oracle3-extras is where new integrations and experimental features for [oracle3](https://github.com/YichengYang-Ethan/oracle3-prediction-market-agent) start. This guide covers where code goes, what every integration needs, and how a feature graduates into oracle3.

## Where code goes

Put code where oracle3 would put it, so that graduating it is a move rather than a rewrite.

| If it is... | It goes in | oracle3 counterpart |
|---|---|---|
| A way to find, align or store relations between markets | `oracle3_extras/market/` | `oracle3.market` |
| A no-arbitrage or pricing check | `oracle3_extras/arbitrage.py` | `oracle3.arbitrage` |
| A `Trader` (execution venue or wallet) | `oracle3_extras/trader/<name>/` | `oracle3.trader` |
| Read-only venue data shared by several features | `oracle3_extras/venues.py` | `oracle3.mcp_server.venues` |

An integration that spans one namespace lives in a subpackage there (`market/kairos/`, `trader/metamask/`) with its client, errors and helpers inside, the way pymc-extras keeps each inference method in its own subpackage. Give it a short public path too (`oracle3_extras/kairos.py`) that re-exports its `__all__`, and add its main entry points to `oracle3_extras/__init__.py`.

Tests mirror the package: `tests/market/kairos/` tests `oracle3_extras/market/kairos/`.

## Checklist for a new integration

1. **Code** in the namespace above, with `__all__` and docstrings that say what the partner's API returns and what the code assumes.
2. **A command group** in `oracle3_extras/cli/<name>.py` that prints one JSON document on stdout.
3. **Tests without network access.** HTTP goes through the `http` fixture (`tests/conftest.py`), CLIs through a scripted fake such as `tests/trader/metamask/fake_mm.py`. Add a contract test marked `live` that calls the real service; it runs only with `pytest --live`.
4. **Safe defaults.** Read-only, paper or testnet first. Real money needs an explicit flag (`--allow-mainnet`, `--yes`) and a cap.
5. **Transparent attribution.** If the integration identifies itself to the partner (a User-Agent, an integration ID, order metadata), document exactly what is sent and how to turn it off. Never send user data.
6. **`docs/<name>/INTEGRATION.md`**: what the integration uses, what was verified and when, open questions for the partner, milestones.
7. **A README section** with setup, a quickstart and a "Relationship with ..." paragraph that implies no endorsement the partner has not given.
8. **A named maintainer** for the integration.

## Graduating into oracle3

A feature can move into oracle3 when:

- people use it, and its API has not changed for a minor release;
- it is tested to oracle3's standard;
- it works without a partner account (an optional key is fine).

Because the namespaces match, the move changes imports, not code: `oracle3_extras.arbitrage.scan_relations` becomes `oracle3.arbitrage.scan_relations`. The extras version stays as a re-export for one minor release.

## Development

```bash
git clone https://github.com/YichengYang-Ethan/oracle3-extras.git
cd oracle3-extras
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest              # offline tests and doctests
pytest --live       # also the contract tests against the real APIs
```

Each release supports one oracle3 minor series, pinned in `pyproject.toml`; bump the pin and run the full suite when oracle3 releases a new minor version. Record every user-visible change in `CHANGELOG.md`.
