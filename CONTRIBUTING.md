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

The move follows the path pymc-extras uses with PyMC (for example `do` and `observe`, which moved into PyMC in 2023):

1. Add the feature to oracle3 at the same module path with the same API, and release it.
2. In oracle3-extras, replace the implementation with a re-export from oracle3 that raises a `FutureWarning` naming the new import, and keep a test that the warning fires.
3. Raise the oracle3 pin in `pyproject.toml` to the release that has the feature.
4. Delete the re-export one minor release later. (pymc-extras removed its re-exports about seven months after the move.)

Because the namespaces match, users only change the import: `oracle3_extras.arbitrage.scan_relations` becomes `oracle3.arbitrage.scan_relations`.

## Renaming or removing public names

Keep the old name working for one minor release and warn, the way pymc-extras handles renames:

```python
# at the bottom of the module that used to define OldName
import warnings

_RENAMED = {'OldName': NewName}


def __getattr__(name):
    if name in _RENAMED:
        warnings.warn(
            f'{name} is deprecated and will be removed in the next minor '
            f'release; use {_RENAMED[name].__name__} instead.',
            FutureWarning,
            stacklevel=2,
        )
        return _RENAMED[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
```

List every rename and removal in `CHANGELOG.md`.

## Development

```bash
git clone https://github.com/YichengYang-Ethan/oracle3-extras.git
cd oracle3-extras
pip install -e ".[dev]"
pre-commit run --all-files   # ruff, formatting, YAML/TOML and whitespace checks, as in CI
pytest                       # offline tests and doctests; warnings fail the run
pytest --live                # also the contract tests against the real APIs
```

Warnings are errors in the test suite, as in pymc-extras: oracle3-extras builds on oracle3 internals, so a deprecation there should surface here first. Silence a third-party warning only in `pyproject.toml`, with a comment saying why.

Each release supports one oracle3 minor series, pinned in `pyproject.toml`; when oracle3 releases a new minor version, raise the pin, run the full suite and release. Record every user-visible change in `CHANGELOG.md`.

## Releasing

Publishing a GitHub release uploads the package to PyPI through `.github/workflows/pypi-publish.yml`, after checking that the tag matches the version and that the wheel and the sdist install and import. The one-time PyPI and GitHub settings, and the release commands, are in [.github/PYPI-SETUP.md](.github/PYPI-SETUP.md).
