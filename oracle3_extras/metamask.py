"""Short public path for :mod:`oracle3_extras.trader.metamask`.

The implementation lives under ``oracle3_extras.trader``, mirroring
``oracle3.trader``; this module re-exports its public API under the shorter
path (the way ``pymc_extras.marginal`` re-exports ``pymc_extras.model.marginal``)
and keeps the 0.1.0 import path working.
"""

from oracle3_extras.trader.metamask import *  # noqa: F403
from oracle3_extras.trader.metamask import __all__  # noqa: F401
