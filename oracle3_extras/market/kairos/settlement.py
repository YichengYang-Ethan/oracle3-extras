"""Did both venues settle each matched pair the same way?

Every cross-venue relation rests on one assumption: the two contracts pay out
together. Ties, postponements and cancellations are where that breaks (one
venue resolves 50-50, the other NO). :func:`check_settlements` asks Kairos's
Market Data API how each side of every relation resolved and compares them:
a ``same_event`` relation needs equal payouts, a ``complement`` relation
payouts that sum to one. Kalshi markets are looked up by ticker, the others by
numeric market id (on 7 October 2026 Kairos resolved Polymarket and
Predict.fun markets by numeric id and nothing by condition id).
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from oracle3.market.relations import MarketRelation

from oracle3_extras._http import APIError
from oracle3_extras.market.kairos.client import KairosClient

__all__ = ['SettlementCheck', 'check_settlements', 'settlement_summary']

_TOLERANCE = 1e-6

logger = logging.getLogger(__name__)


#: Venues whose settlement results Kairos serves.
SETTLEMENT_VENUES = ('kalshi', 'polymarket', 'predictfun', 'hyperliquid')


def _renamed(old: str, new: str) -> None:
    warnings.warn(
        f'SettlementCheck.{old} is deprecated and will be removed in the next minor '
        f'release; use SettlementCheck.{new} instead.',
        FutureWarning,
        stacklevel=3,
    )


@dataclass
class SettlementCheck:
    """How one relation's two markets resolved.

    ``first`` is the share of market A's payout that went to its first outcome
    (YES on Kalshi), ``second`` the same for market B; ``None`` means not
    resolved yet.
    """

    relation: MarketRelation
    first: float | None = None
    second: float | None = None

    @property
    def kalshi(self) -> float | None:
        """Deprecated name of :attr:`first` (oracle3-extras 0.3)."""
        _renamed('kalshi', 'first')
        return self.first

    @property
    def polymarket(self) -> float | None:
        """Deprecated name of :attr:`second` (oracle3-extras 0.3)."""
        _renamed('polymarket', 'second')
        return self.second

    @property
    def settled(self) -> bool:
        return self.first is not None and self.second is not None

    @property
    def expected_second(self) -> float | None:
        """Market B's first-outcome payout that agrees with market A's result."""
        if self.first is None:
            return None
        if self.relation.spread_type == 'complement':
            return 1.0 - self.first
        return self.first

    @property
    def agrees(self) -> bool | None:
        if not self.settled:
            return None
        return abs(self.second - self.expected_second) <= _TOLERANCE  # type: ignore[operator]

    def to_dict(self) -> dict[str, Any]:
        a, b = self.relation.market_a, self.relation.market_b
        return {
            'relation_id': self.relation.relation_id,
            'relation': self.relation.spread_type,
            'venues': f"{a.get('venue')} + {b.get('venue')}",
            'market_a': a.get('name'),
            'market_b': b.get('name'),
            'a_first_outcome_paid': self.first,
            'b_first_outcome_paid': self.second,
            'agrees': self.agrees,
        }


async def check_settlements(
    relations: Sequence[MarketRelation], client: KairosClient | None = None
) -> list[SettlementCheck]:
    """Look up both sides' results for every relation between venues Kairos settles."""
    client = client or KairosClient()
    pairs = [
        r
        for r in relations
        if r.market_a.get('venue') in SETTLEMENT_VENUES
        and r.market_b.get('venue') in SETTLEMENT_VENUES
        and r.spread_type in ('same_event', 'complement')
    ]
    wanted: dict[str, set[str]] = {venue: set() for venue in SETTLEMENT_VENUES}
    for r in pairs:
        wanted[r.market_a['venue']].add(str(r.market_a['market_id']))
        wanted[r.market_b['venue']].add(str(r.market_b['market_id']))
    results: dict[str, dict[str, float]] = {}
    for venue, ids in wanted.items():
        try:
            results[venue] = await client.resolutions(venue, sorted(ids)) if ids else {}
        except APIError as exc:
            # One venue's outage leaves its markets unresolved, not the whole check.
            logger.warning('Kairos resolutions for %s failed: %s', venue, exc)
            results[venue] = {}
    return [
        SettlementCheck(
            relation=r,
            first=results[r.market_a['venue']].get(str(r.market_a['market_id'])),
            second=results[r.market_b['venue']].get(str(r.market_b['market_id'])),
        )
        for r in pairs
    ]


def settlement_summary(checks: Sequence[SettlementCheck]) -> dict[str, Any]:
    """Counts of agreeing, disagreeing and unresolved relations, with the mismatches."""
    settled = [c for c in checks if c.settled]
    disagree = [c for c in settled if not c.agrees]
    return {
        'checked': len(checks),
        'settled': len(settled),
        'pending': len(checks) - len(settled),
        'agree': len(settled) - len(disagree),
        'disagree': len(disagree),
        'agreement_rate': round((len(settled) - len(disagree)) / len(settled), 4)
        if settled
        else None,
        'mismatches': [c.to_dict() for c in disagree],
    }
