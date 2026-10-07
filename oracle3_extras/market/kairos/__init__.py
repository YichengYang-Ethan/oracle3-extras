"""Kairos cross-venue matched markets as oracle3 relations.

Kairos (https://kairos.trade) matches the same market across Kalshi,
Polymarket, Predict.fun and Hyperliquid and publishes the pairs through a
public Data API. This module reads that catalog, lines up the outcomes of each
Kalshi–Polymarket pair and returns oracle3 ``same_event`` or ``complement``
relations, ready for ``oracle3_extras.arbitrage.scan_relations`` or oracle3's
relation store.
"""

from oracle3_extras.market.kairos.client import (
    DATA_API,
    MIN_SIMILARITY,
    KairosClient,
    KairosCredentials,
    KairosMarket,
    MatchedMarkets,
    MatchedPair,
)
from oracle3_extras.market.kairos.relations import (
    RELATION_PREFIX,
    KairosRelations,
    kairos_relations,
    save_relations,
    to_relation,
)

__all__ = [
    'DATA_API',
    'MIN_SIMILARITY',
    'RELATION_PREFIX',
    'KairosClient',
    'KairosCredentials',
    'KairosMarket',
    'KairosRelations',
    'MatchedMarkets',
    'MatchedPair',
    'kairos_relations',
    'save_relations',
    'to_relation',
]
