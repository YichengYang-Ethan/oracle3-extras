"""Short public path for :mod:`oracle3_extras.market.tickers`.

The implementation lives under ``oracle3_extras.market``, mirroring
``oracle3.market``; this module re-exports its public API under the shorter
path, the way ``pymc_extras.marginal`` re-exports ``pymc_extras.model.marginal``.
"""

from oracle3_extras.market.tickers import *  # noqa: F403
from oracle3_extras.market.tickers import __all__  # noqa: F401
