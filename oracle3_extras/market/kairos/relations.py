"""Turn Kairos matched pairs into oracle3 market relations.

:func:`kairos_relations` fetches the Kairos catalog, looks up both sides of
every Kalshi–Polymarket pair on the venues themselves, lines up their outcomes
with :func:`oracle3_extras.market.align.align_kalshi_polymarket` and returns
one ``oracle3.market.MarketRelation`` per aligned pair. Pairs that cannot be
aligned come back with the reason, so the catalog's coverage stays visible.

Market dicts follow oracle3's ticker fields (``KalshiTicker``,
``PolyMarketTicker``) and carry ``venue`` and ``market_id``, so a stored
relation can be passed straight to oracle3's ``check_constraint_live`` tool.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oracle3.market.relations import RELATIONS_PATH, MarketRelation

from oracle3_extras import venues
from oracle3_extras.market.align import (
    OutcomeAlignment,
    align_kalshi_polymarket,
    align_two_outcome_markets,
    utc_time,
)
from oracle3_extras.market.kairos.client import (
    KairosClient,
    KairosMarket,
    MatchedMarkets,
    MatchedPair,
)
from oracle3_extras.market.kairos.markets import as_polymarket_shape

__all__ = [
    'RELATION_PREFIX',
    'VENUES',
    'KairosRelations',
    'build_relation',
    'kairos_relations',
    'relation_id',
    'save_relations',
    'to_relation',
]

#: Prefix of every relation id this module writes.
RELATION_PREFIX = 'kairos:'

#: Venues Kairos matches, in the order that decides which side is market A.
VENUES = ('kalshi', 'polymarket', 'predictfun', 'hyperliquid')

_NAMES = {
    'kalshi': 'Kalshi',
    'polymarket': 'Polymarket',
    'predictfun': 'Predict.fun',
    'hyperliquid': 'Hyperliquid',
}

_HYPOTHESIS = {'same_event': 'P(A) = P(B)', 'complement': 'P(A) + P(B) = 1'}

logger = logging.getLogger(__name__)


@dataclass
class KairosRelations:
    """Aligned relations from one Kairos catalog snapshot, and what was left out."""

    catalog: MatchedMarkets
    relations: list[MarketRelation] = field(default_factory=list)
    rejected: list[tuple[MatchedPair, str]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        venue_pairs = Counter(
            ' + '.join(sorted(p.providers)) for p in self.catalog.pairs
        )
        aligned_pairs = Counter(
            f"{r.market_a.get('venue')} + {r.market_b.get('venue')}"
            for r in self.relations
        )
        types = Counter(r.spread_type for r in self.relations)
        reasons = Counter(_reason_group(reason) for _, reason in self.rejected)
        kalshi_poly = frozenset({'kalshi', 'polymarket'})
        return {
            'catalog_version': self.catalog.catalog_version,
            'fetched_at': self.catalog.fetched_at,
            'pairs': len(self.catalog.pairs),
            'pairs_by_venues': dict(venue_pairs.most_common()),
            'considered': len(self.relations) + len(self.rejected),
            'kalshi_polymarket': sum(
                1
                for r in self.relations
                if {r.market_a.get('venue'), r.market_b.get('venue')} == kalshi_poly
            )
            + sum(1 for p, _ in self.rejected if p.providers == kalshi_poly),
            'aligned': len(self.relations),
            'aligned_by_venues': dict(aligned_pairs.most_common()),
            'same_event': types.get('same_event', 0),
            'complement': types.get('complement', 0),
            'with_warnings': sum(
                1 for r in self.relations if r.analysis_b.get('warnings')
            ),
            'rejected': dict(reasons.most_common()),
        }


def _reason_group(reason: str) -> str:
    """Collapse per-pair details (lines, times) so reasons can be counted."""
    return reason.split(':')[0]


def _open(venue: str, market: Mapping[str, Any]) -> str:
    """Why a market no longer trades, or an empty string if it does."""
    if venue == 'kalshi':
        if str(market.get('status') or '') not in ('active', 'open'):
            return f"Kalshi market is {market.get('status') or 'not open'}"
        return ''
    if market.get('closed') or market.get('active') is False:
        return f'{_NAMES[venue]} market is closed'
    if market.get('acceptingOrders') is False:
        return f'{_NAMES[venue]} market is not accepting orders'
    return ''


def relation_id(a_venue: str, a_id: str, b_venue: str, b_id: str) -> str:
    """``kairos:<ticker>:<market id>`` for Kalshi–Polymarket (as since 0.2), else venue-tagged."""
    if (a_venue, b_venue) == ('kalshi', 'polymarket'):
        return f'{RELATION_PREFIX}{a_id}:{b_id}'
    return f'{RELATION_PREFIX}{a_venue}:{a_id}:{b_venue}:{b_id}'


def _market_dict(venue: str, market: Mapping[str, Any]) -> dict[str, Any]:
    """oracle3's market fields for one side; ``venue`` and ``market_id`` always set."""
    if venue == 'kalshi':
        ticker = str(market['ticker'])
        return {
            'venue': 'kalshi',
            'market_id': ticker,
            'symbol': ticker,
            'name': str(market.get('title') or ''),
            'market_ticker': ticker,
            'event_ticker': str(market.get('event_ticker') or ''),
            'series_ticker': ticker.split('-')[0],
            'yes_outcome': str(
                market.get('yes_sub_title') or market.get('title') or ''
            ),
            'expected_expiration': str(market.get('expected_expiration_time') or ''),
            'outcomes': ['Yes', 'No'],
        }
    tokens = venues.polymarket_token_ids(market)
    start = utc_time(market.get('gameStartTime'))
    out = {
        'venue': venue,
        'market_id': str(market.get('id') or ''),
        'symbol': tokens[0] if tokens else '',
        'name': str(market.get('question') or ''),
        'token_id': tokens[0] if tokens else '',
        'no_token_id': tokens[1] if len(tokens) > 1 else '',
        'condition_id': str(market.get('conditionId') or ''),
        'slug': str(market.get('slug') or ''),
        'outcomes': [
            str(o)
            for o in market.get('outcomeLabels') or venues.polymarket_outcomes(market)
        ],
        'game_start': start.isoformat() if start else '',
    }
    if venue != 'polymarket':
        event_date = utc_time(market.get('eventDate'))
        out['event_date'] = event_date.isoformat() if event_date else ''
        out['fee_rate_bps'] = market.get('feeRateBps')
    return out


def to_relation(
    pair: MatchedPair,
    kalshi: Mapping[str, Any],
    polymarket: Mapping[str, Any],
    alignment: OutcomeAlignment,
) -> MarketRelation:
    """One oracle3 relation for an aligned Kalshi–Polymarket pair (Kalshi is market A)."""
    return build_relation(
        pair, ('kalshi', kalshi), ('polymarket', polymarket), alignment
    )


def build_relation(
    pair: MatchedPair,
    first: tuple[str, Mapping[str, Any]],
    second: tuple[str, Mapping[str, Any]],
    alignment: OutcomeAlignment,
) -> MarketRelation:
    """One oracle3 relation for an aligned pair of ``(venue, market)`` sides.

    Market A's first outcome (YES on Kalshi) pays exactly when market B's
    outcome ``alignment.outcome_index`` pays: ``same_event`` for index 0,
    ``complement`` for index 1.
    """
    if not alignment.aligned or alignment.relation is None:
        raise ValueError(f'pair is not aligned: {alignment.problem}')
    a_venue, a_market = first
    b_venue, b_market = second
    market_a, market_b = (
        _market_dict(a_venue, a_market),
        _market_dict(b_venue, b_market),
    )
    index = alignment.outcome_index or 0
    a_label = (
        f"YES ({market_a['yes_outcome']})"
        if a_venue == 'kalshi'
        else f"outcome 0 ({market_a['outcomes'][0] if market_a['outcomes'] else '?'})"
    )
    b_outcomes = market_b['outcomes']
    closes = [
        str(t)
        for t in (
            a_market.get('close_time'),
            a_market.get('endDate'),
            b_market.get('close_time'),
            b_market.get('endDate'),
        )
        if t
    ]
    categories = sorted({c for c in (pair.a.category, pair.b.category) if c})
    return MarketRelation(
        relation_id=relation_id(
            a_venue, market_a['market_id'], b_venue, market_b['market_id']
        ),
        market_a=market_a,
        market_b=market_b,
        spread_type=alignment.relation,
        confidence=pair.similarity,
        reasoning=(
            f'Kairos matched these markets (similarity {pair.similarity:.2f}). '
            f'{_NAMES[a_venue]} {a_label} is {_NAMES[b_venue]} outcome {index} '
            f'({b_outcomes[index] if index < len(b_outcomes) else "?"}); '
            f'checked by {", ".join(alignment.evidence)}. '
            + ''.join(f'Warning: {w}. ' for w in alignment.warnings)
            + 'Read both rulebooks before trading: ties, postponements and '
            'cancellations can settle differently.'
        ),
        hypothesis=_HYPOTHESIS[alignment.relation],
        analysis_a={'source': 'kairos', 'kind': alignment.kind},
        analysis_b={
            'source': 'kairos',
            'kairos_updated_at': pair.updated_at,
            'kairos_categories': categories,
            'evidence': list(alignment.evidence),
            'warnings': list(alignment.warnings),
        },
        valid_until=min(closes) if closes else None,
    )


def _ordered(
    pair: MatchedPair, venues_: Sequence[str]
) -> tuple[KairosMarket, KairosMarket] | None:
    if not pair.providers <= set(venues_) or len(pair.providers) != 2:
        return None
    first, second = sorted((pair.a, pair.b), key=lambda m: VENUES.index(m.provider))
    return first, second


async def _lookup(
    venue: str, ids: list[str], client: KairosClient, include_closed: bool
) -> dict[str, dict[str, Any]]:
    """Market objects for one venue: from Kalshi and Polymarket, or from Kairos."""
    if not ids:
        return {}
    if venue == 'kalshi':
        return await venues.kalshi_markets(ids)
    if venue == 'polymarket':
        return await venues.polymarket_markets(ids, include_closed=include_closed)
    found = await client.markets(venue, ids)
    return {mid: as_polymarket_shape(m, venue) for mid, m in found.items()}


async def kairos_relations(
    client: KairosClient | None = None,
    *,
    catalog: MatchedMarkets | None = None,
    venues: Sequence[str] = VENUES,
    min_similarity: float | None = None,
    include_closed: bool = False,
) -> KairosRelations:
    """Fetch the Kairos catalog and align every pair between the given venues.

    Args:
        client: Kairos client; a default anonymous client if omitted.
        catalog: Align this catalog snapshot instead of fetching one.
        venues: Venues to include (any of :data:`VENUES`).
        min_similarity: Kairos similarity floor (never below 0.82).
        include_closed: Keep pairs whose markets no longer trade (needed to
            align an older snapshot whose games have finished).
    """
    unknown = set(venues) - set(VENUES)
    if unknown:
        raise ValueError(f'unknown venues {sorted(unknown)}; expected some of {VENUES}')
    client = client or KairosClient()
    if catalog is None:
        provider = 'kalshi' if set(venues) == {'kalshi', 'polymarket'} else None
        catalog = await client.matched_markets(
            provider=provider, min_similarity=min_similarity
        )
    ordered = [
        (pair, sides) for pair in catalog.pairs if (sides := _ordered(pair, venues))
    ]
    ids: dict[str, list[str]] = {v: [] for v in VENUES}
    for _, (first, second) in ordered:
        ids[first.provider].append(first.market_id)
        ids[second.provider].append(second.market_id)
    found = {v: await _lookup(v, ids[v], client, include_closed) for v in VENUES}

    result = KairosRelations(catalog=catalog)
    seen: set[str] = set()
    for pair, (first, second) in ordered:
        a = found[first.provider].get(first.market_id)
        b = found[second.provider].get(second.market_id)
        if a is None or b is None:
            missing = first.provider if a is None else second.provider
            result.rejected.append((pair, f'{_NAMES[missing]} market not found'))
            continue
        problem = (
            ''
            if include_closed
            else (_open(first.provider, a) or _open(second.provider, b))
        )
        if problem:
            result.rejected.append((pair, problem))
            continue
        if first.provider == 'kalshi':
            alignment = align_kalshi_polymarket(a, b)
        else:
            alignment = align_two_outcome_markets(a, b)
        if not alignment.aligned:
            result.rejected.append((pair, alignment.problem))
            continue
        relation = build_relation(
            pair, (first.provider, a), (second.provider, b), alignment
        )
        if relation.relation_id not in seen:
            seen.add(relation.relation_id)
            result.relations.append(relation)
    logger.info('Kairos: aligned %d of %d pairs', len(result.relations), len(ordered))
    return result


def save_relations(
    relations: Iterable[MarketRelation],
    path: Path | str = RELATIONS_PATH,
    *,
    prune: bool = True,
) -> dict[str, int]:
    """Merge Kairos relations into an oracle3 relation store in one write.

    oracle3's ``RelationStore.add`` rewrites the whole file per relation, which
    is slow for thousands of pairs; this reads ``relations.json`` once, merges
    and writes it back atomically in the same format.

    Relations already in the store keep their lifecycle status and validation
    results; only market data, confidence and expiry are refreshed. With
    ``prune``, Kairos relations still at ``discovered`` that are no longer in
    the catalog are removed. Relations from other sources are never touched.
    """
    path = Path(path)
    existing: list[dict[str, Any]] = []
    if path.exists():
        existing = json.loads(path.read_text() or '[]')
    incoming = {r.relation_id: r for r in relations}
    counts = {'added': 0, 'updated': 0, 'removed': 0, 'kept': 0}
    merged: list[dict[str, Any]] = []
    for row in existing:
        rid = str(row.get('relation_id', ''))
        fresh = incoming.pop(rid, None)
        if fresh is not None:
            row = {
                **row,
                **{
                    k: v
                    for k, v in fresh.to_dict().items()
                    if k not in ('status', 'validation', 'last_validated', 'created_at')
                },
            }
            counts['updated'] += 1
        elif (
            prune
            and rid.startswith(RELATION_PREFIX)
            and row.get('status') == 'discovered'
        ):
            counts['removed'] += 1
            continue
        else:
            counts['kept'] += 1
        merged.append(row)
    for relation in incoming.values():
        merged.append(relation.to_dict())
        counts['added'] += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(merged, indent=2, default=str))
    os.replace(tmp, path)
    return counts
