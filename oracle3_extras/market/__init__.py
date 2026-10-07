"""Market relations, mirroring ``oracle3.market``.

- :mod:`oracle3_extras.market.align`: line up a Kalshi market and a Polymarket
  market as ``same_event`` or ``complement``.
- :mod:`oracle3_extras.market.kairos`: Kairos's cross-venue catalog as oracle3
  relations, with price history and settlement checks.
- :mod:`oracle3_extras.market.archive`: keep relations after their source
  stops listing them.
"""

from oracle3_extras.market import kairos
from oracle3_extras.market.align import OutcomeAlignment, align_kalshi_polymarket
from oracle3_extras.market.archive import load_archive, started_between, update_archive

__all__ = [
    'OutcomeAlignment',
    'align_kalshi_polymarket',
    'kairos',
    'load_archive',
    'started_between',
    'update_archive',
]
