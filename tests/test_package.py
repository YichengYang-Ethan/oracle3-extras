"""The public API and the namespace policy: every subpackage mirrors an oracle3 module."""

from __future__ import annotations

import importlib

import pytest

import oracle3_extras as o3x


def test_top_level_api() -> None:
    for name in o3x.__all__:
        assert hasattr(o3x, name), name
    assert o3x.__version__[0].isdigit()


@pytest.mark.parametrize('name', ['arbitrage', 'market', 'trader'])
def test_namespaces_mirror_oracle3(name: str) -> None:
    importlib.import_module(f'oracle3.{name}')
    importlib.import_module(f'oracle3_extras.{name}')


@pytest.mark.parametrize(
    ('short', 'full'),
    [
        ('oracle3_extras.kairos', 'oracle3_extras.market.kairos'),
        ('oracle3_extras.metamask', 'oracle3_extras.trader.metamask'),
    ],
)
def test_short_paths_reexport_the_same_objects(short: str, full: str) -> None:
    a, b = importlib.import_module(short), importlib.import_module(full)
    assert a.__all__ == b.__all__
    for name in b.__all__:
        assert getattr(a, name) is getattr(b, name)
