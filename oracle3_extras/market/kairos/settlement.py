"""Did both venues settle each matched pair the same way?

Every cross-venue relation rests on one assumption: the two contracts pay out
together. Ties, postponements and cancellations are where that breaks (one
venue resolves 50-50, the other NO). :func:`check_settlements` asks Kairos's
Market Data API how each side of every relation resolved and compares them:
a ``same_event`` relation needs equal payouts, a ``complement`` relation
payouts that sum to one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from oracle3.market.relations import MarketRelation

from oracle3_extras.market.kairos.client import KairosClient

__all__ = ['SettlementCheck', 'check_settlements', 'settlement_summary']

_TOLERANCE = 1e-6


@dataclass
class SettlementCheck:
    """How one relation's two markets resolved.

    ``kalshi`` is the share of the payout that went to Kalshi YES and
    ``polymarket`` the share that went to the Polymarket market's first
    outcome; ``None`` means not resolved yet.
    """

    relation: MarketRelation
    kalshi: float | None = None
    polymarket: float | None = None

    @property
    def settled(self) -> bool:
        return self.kalshi is not None and self.polymarket is not None

    @property
    def expected_polymarket(self) -> float | None:
        """The first-outcome payout that agrees with Kalshi's result."""
        if self.kalshi is None:
            return None
        if self.relation.spread_type == 'complement':
            return 1.0 - self.kalshi
        return self.kalshi

    @property
    def agrees(self) -> bool | None:
        if not self.settled:
            return None
        return abs(self.polymarket - self.expected_polymarket) <= _TOLERANCE  # type: ignore[operator]

    def to_dict(self) -> dict[str, Any]:
        return {
            'relation_id': self.relation.relation_id,
            'relation': self.relation.spread_type,
            'kalshi_market': self.relation.market_a.get('name'),
            'polymarket_market': self.relation.market_b.get('name'),
            'kalshi_yes_paid': self.kalshi,
            'polymarket_first_outcome_paid': self.polymarket,
            'agrees': self.agrees,
        }


async def check_settlements(
    relations: Sequence[MarketRelation], client: KairosClient | None = None
) -> list[SettlementCheck]:
    """Look up both sides' results for every Kalshi–Polymarket relation."""
    client = client or KairosClient()
    pairs = [
        r
        for r in relations
        if r.market_a.get('venue') == 'kalshi'
        and r.market_b.get('venue') == 'polymarket'
        and r.spread_type in ('same_event', 'complement')
    ]
    kalshi = await client.resolutions(
        'kalshi', (r.market_a['market_id'] for r in pairs)
    )
    poly = await client.resolutions(
        'polymarket', (r.market_b['market_id'] for r in pairs)
    )
    return [
        SettlementCheck(
            relation=r,
            kalshi=kalshi.get(str(r.market_a['market_id'])),
            polymarket=poly.get(str(r.market_b['market_id'])),
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
