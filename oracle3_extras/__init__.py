"""oracle3-extras: integrations and experimental features for oracle3.

The package mirrors oracle3's namespaces, so code moves between the two
without rewrites::

    import oracle3_extras as o3x

    result = await o3x.kairos_relations()            # oracle3_extras.market.kairos
    report = await o3x.scan_relations(result.relations)  # oracle3_extras.arbitrage
    trader = o3x.AgentWalletTrader(...)               # oracle3_extras.trader.metamask

Features that prove widely useful graduate into oracle3 itself.
"""

import logging

from oracle3_extras import arbitrage, market, trader, venues
from oracle3_extras._version import __version__
from oracle3_extras.arbitrage import scan_relations, walk_books
from oracle3_extras.market.align import align_kalshi_polymarket
from oracle3_extras.market.kairos import KairosClient, kairos_relations
from oracle3_extras.trader.metamask import AgentWalletClient, AgentWalletTrader

__all__ = [
    'AgentWalletClient',
    'AgentWalletTrader',
    'KairosClient',
    '__version__',
    'align_kalshi_polymarket',
    'arbitrage',
    'kairos_relations',
    'market',
    'scan_relations',
    'trader',
    'venues',
    'walk_books',
]

_log = logging.getLogger('oracle3_extras')

if not logging.root.handlers:
    _log.setLevel(logging.INFO)
    if not _log.handlers:
        _log.addHandler(logging.StreamHandler())
