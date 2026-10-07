"""Market relations, mirroring ``oracle3.market``.

- :mod:`oracle3_extras.market.align`: line up a Kalshi market and a Polymarket
  market as ``same_event`` or ``complement``.
- :mod:`oracle3_extras.market.kairos`: Kairos's cross-venue catalog as oracle3
  relations.
"""

from oracle3_extras.market import kairos
from oracle3_extras.market.align import OutcomeAlignment, align_kalshi_polymarket

__all__ = ['OutcomeAlignment', 'align_kalshi_polymarket', 'kairos']
