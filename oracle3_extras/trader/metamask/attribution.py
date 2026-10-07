"""Transparent, opt-out attribution for orders routed through Agent Wallet.

Open-source trading connectors that partner with venues tag the orders they
route: Hummingbot's fee-share partners recognise its traffic by an API header,
and CCXT attaches broker IDs. CCXT also showed the cost of doing this quietly,
so this module follows two rules: attribution is documented in the README, and
users can turn it off with ``ORACLE3_ATTRIBUTION=off``. Attribution never
changes the price, size or fees of an order.

Two channels carry the integration ID:

1. ``MM_INTEGRATION_ID`` and ``MM_INTEGRATION_VERSION`` are set on every ``mm``
   subprocess. The CLI does not read them yet; they are the proposed
   counterpart of ``MM_PLUGIN_HOST``, which it reads today to attribute agent
   hosts such as Claude Code or Cursor.
2. The Polymarket CLOB V2 order ``metadata`` field (bytes32). MetaMask's own
   builder code stays in the separate ``builder`` field, so MetaMask keeps fee
   attribution and the integration ID is readable on-chain by anyone. It is
   sent only when the installed CLI exposes ``--metadata`` on
   ``mm predict place``; the Agent Wallet SDK already accepts the field.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from oracle3_extras._version import __version__

INTEGRATION_ID = 'oracle3'
ATTRIBUTION_ENV = 'ORACLE3_ATTRIBUTION'
_OFF_VALUES = frozenset({'off', '0', 'false', 'no'})


def metadata_bytes32(integration_id: str = INTEGRATION_ID) -> str:
    """Encode an integration ID as a right-padded ASCII bytes32 hex string.

    ASCII rather than a hash keeps the tag human-readable on a block explorer:
    ``oracle3`` becomes ``0x6f7261636c6533`` followed by zero bytes.
    """
    raw = integration_id.encode('ascii')
    if not raw or len(raw) > 32:
        raise ValueError('integration_id must be 1-32 ASCII characters')
    return '0x' + raw.hex().ljust(64, '0')


@dataclass(frozen=True)
class Attribution:
    """Which integration to credit, and whether to send attribution at all."""

    integration_id: str = INTEGRATION_ID
    version: str = __version__
    enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Attribution:
        """Read the opt-out switch from ``ORACLE3_ATTRIBUTION``."""
        source = os.environ if env is None else env
        value = source.get(ATTRIBUTION_ENV, '').strip().lower()
        return cls(enabled=value not in _OFF_VALUES)

    def env(self) -> dict[str, str]:
        """Environment variables added to each ``mm`` call."""
        if not self.enabled:
            return {}
        return {
            'MM_INTEGRATION_ID': self.integration_id,
            'MM_INTEGRATION_VERSION': self.version,
        }

    @property
    def metadata(self) -> str | None:
        """The bytes32 order metadata, or ``None`` when attribution is off."""
        return metadata_bytes32(self.integration_id) if self.enabled else None
