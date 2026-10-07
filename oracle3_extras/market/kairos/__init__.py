"""Kairos cross-venue matched markets as oracle3 relations.

Kairos (https://kairos.trade) matches the same market across Kalshi,
Polymarket, Predict.fun and Hyperliquid and publishes the pairs through a
public Data API. This module reads that catalog, lines up the outcomes of each
Kalshi–Polymarket pair and returns oracle3 ``same_event`` or ``complement``
relations, ready for ``oracle3_extras.arbitrage.scan_relations`` or oracle3's
relation store. With Kairos's Market Data API it also compares how the two
venues traded each pair (:func:`price_history`) and settled it
(:func:`check_settlements`).
"""

from oracle3_extras.market.kairos.client import (
    DATA_API,
    MARKET_DATA_API,
    MIN_SIMILARITY,
    Candle,
    CandleRequest,
    CandleSeries,
    KairosClient,
    KairosCredentials,
    KairosMarket,
    MatchedMarkets,
    MatchedPair,
)
from oracle3_extras.market.kairos.history import (
    PairHistory,
    history_summary,
    price_history,
)
from oracle3_extras.market.kairos.relations import (
    RELATION_PREFIX,
    KairosRelations,
    kairos_relations,
    save_relations,
    to_relation,
)
from oracle3_extras.market.kairos.settlement import (
    SettlementCheck,
    check_settlements,
    settlement_summary,
)

__all__ = [
    'DATA_API',
    'MARKET_DATA_API',
    'MIN_SIMILARITY',
    'RELATION_PREFIX',
    'Candle',
    'CandleRequest',
    'CandleSeries',
    'KairosClient',
    'KairosCredentials',
    'KairosMarket',
    'KairosRelations',
    'MatchedMarkets',
    'MatchedPair',
    'PairHistory',
    'SettlementCheck',
    'check_settlements',
    'history_summary',
    'kairos_relations',
    'price_history',
    'save_relations',
    'settlement_summary',
    'to_relation',
]
