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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oracle3.market.relations import RELATIONS_PATH, MarketRelation

from oracle3_extras import venues
from oracle3_extras.market.align import OutcomeAlignment, align_kalshi_polymarket
from oracle3_extras.market.kairos.client import (
    KairosClient,
    MatchedMarkets,
    MatchedPair,
)

__all__ = [
    'RELATION_PREFIX',
    'KairosRelations',
    'kairos_relations',
    'save_relations',
    'to_relation',
]

#: Prefix of every relation id this module writes, ``kairos:<ticker>:<market id>``.
RELATION_PREFIX = 'kairos:'

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
        types = Counter(r.spread_type for r in self.relations)
        reasons = Counter(_reason_group(reason) for _, reason in self.rejected)
        return {
            'catalog_version': self.catalog.catalog_version,
            'fetched_at': self.catalog.fetched_at,
            'pairs': len(self.catalog.pairs),
            'pairs_by_venues': dict(venue_pairs.most_common()),
            'kalshi_polymarket': len(self.relations) + len(self.rejected),
            'aligned': len(self.relations),
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


def _open(kalshi: Mapping[str, Any], poly: Mapping[str, Any]) -> str:
    if str(kalshi.get('status') or '') not in ('active', 'open'):
        return f"Kalshi market is {kalshi.get('status') or 'not open'}"
    if poly.get('closed') or poly.get('active') is False:
        return 'Polymarket market is closed'
    if poly.get('acceptingOrders') is False:
        return 'Polymarket market is not accepting orders'
    return ''


def to_relation(
    pair: MatchedPair,
    kalshi: Mapping[str, Any],
    polymarket: Mapping[str, Any],
    alignment: OutcomeAlignment,
) -> MarketRelation:
    """One oracle3 relation for an aligned pair (Kalshi is always market A)."""
    if not alignment.aligned or alignment.relation is None:
        raise ValueError(f'pair is not aligned: {alignment.problem}')
    ticker = str(kalshi['ticker'])
    market_id = str(polymarket['id'])
    tokens = venues.polymarket_token_ids(polymarket)
    outcomes = venues.polymarket_outcomes(polymarket)
    label = str(kalshi.get('yes_sub_title') or kalshi.get('title') or '')
    index = alignment.outcome_index or 0
    expiries = [
        str(t) for t in (kalshi.get('close_time'), polymarket.get('endDate')) if t
    ]
    return MarketRelation(
        relation_id=f'{RELATION_PREFIX}{ticker}:{market_id}',
        market_a={
            'venue': 'kalshi',
            'market_id': ticker,
            'symbol': ticker,
            'name': str(kalshi.get('title') or ''),
            'market_ticker': ticker,
            'event_ticker': str(kalshi.get('event_ticker') or ''),
            'series_ticker': ticker.split('-')[0],
            'yes_outcome': label,
        },
        market_b={
            'venue': 'polymarket',
            'market_id': market_id,
            'symbol': tokens[0] if tokens else '',
            'name': str(polymarket.get('question') or ''),
            'token_id': tokens[0] if tokens else '',
            'no_token_id': tokens[1] if len(tokens) > 1 else '',
            'condition_id': str(polymarket.get('conditionId') or ''),
            'slug': str(polymarket.get('slug') or ''),
            'outcomes': outcomes,
        },
        spread_type=alignment.relation,
        confidence=pair.similarity,
        reasoning=(
            f'Kairos matched these markets (similarity {pair.similarity:.2f}). '
            f'Kalshi YES ({label}) is Polymarket outcome {index} '
            f'({outcomes[index] if index < len(outcomes) else "?"}); '
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
            'evidence': list(alignment.evidence),
            'warnings': list(alignment.warnings),
        },
        valid_until=min(expiries) if expiries else None,
    )


async def kairos_relations(
    client: KairosClient | None = None,
    *,
    min_similarity: float | None = None,
    include_closed: bool = False,
) -> KairosRelations:
    """Fetch the Kairos catalog and align every Kalshi–Polymarket pair.

    Args:
        client: Kairos client; a default anonymous client if omitted.
        min_similarity: Kairos similarity floor (never below 0.82).
        include_closed: Keep pairs whose markets no longer trade.
    """
    client = client or KairosClient()
    catalog = await client.matched_markets(
        provider='kalshi', min_similarity=min_similarity
    )
    pairs = catalog.between('kalshi', 'polymarket')
    kalshi_ids = [p.side('kalshi').market_id for p in pairs]  # type: ignore[union-attr]
    poly_ids = [p.side('polymarket').market_id for p in pairs]  # type: ignore[union-attr]
    kalshi = await venues.kalshi_markets(kalshi_ids)
    poly = await venues.polymarket_markets(poly_ids)

    result = KairosRelations(catalog=catalog)
    seen: set[str] = set()
    for pair, kalshi_id, poly_id in zip(pairs, kalshi_ids, poly_ids, strict=True):
        k, pm = kalshi.get(kalshi_id), poly.get(poly_id)
        if k is None or pm is None:
            missing = 'Kalshi' if k is None else 'Polymarket'
            result.rejected.append((pair, f'{missing} market not found'))
            continue
        problem = '' if include_closed else _open(k, pm)
        if problem:
            result.rejected.append((pair, problem))
            continue
        alignment = align_kalshi_polymarket(k, pm)
        if not alignment.aligned:
            result.rejected.append((pair, alignment.problem))
            continue
        relation = to_relation(pair, k, pm, alignment)
        if relation.relation_id not in seen:
            seen.add(relation.relation_id)
            result.relations.append(relation)
    logger.info(
        'Kairos: aligned %d of %d Kalshi–Polymarket pairs',
        len(result.relations),
        len(pairs),
    )
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
