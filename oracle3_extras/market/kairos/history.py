"""How far apart two venues traded the same event, from Kairos candles.

:func:`price_history` fetches candles for both sides of each relation from
Kairos's Market Data API (Kalshi, Polymarket, Predict.fun or Hyperliquid) and
keeps the buckets in which both venues traded. Market A's price is that of
its first outcome (YES on Kalshi); market B's is that of the outcome that pays
with it: the first for a ``same_event`` relation, the second for a
``complement``. The gap is market A's last trade minus market B's in the same
bucket.

:func:`recent_history` does the same over a fixed recent window (by default
the last 24 hours in one-hour buckets, up to the event's start), which suits
markets that run for months: elections, crypto, the economy.

These are trade prices, not quotes. A gap shows the venues disagreeing; it is
not an arbitrage that could have been executed (Kairos publishes no historical
order books), and two trades in the same bucket can be up to a bucket apart.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from oracle3.market.relations import MarketRelation

from oracle3_extras.market.archive import event_time
from oracle3_extras.market.kairos.client import CandleRequest, KairosClient

__all__ = ['PairHistory', 'history_summary', 'price_history', 'recent_history']

#: A gap at least this large (in dollars) counts as wide in the summaries.
WIDE_GAP = 0.02


def _percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile; ``None`` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return round(ordered[rank - 1], 6)


def _stats(gaps: Sequence[float]) -> dict[str, Any]:
    sizes = [abs(g) for g in gaps]
    return {
        'minutes': len(sizes),
        'median_abs_gap': _percentile(sizes, 0.5),
        'p90_abs_gap': _percentile(sizes, 0.9),
        'max_abs_gap': round(max(sizes), 6) if sizes else None,
        'share_wide': round(sum(s >= WIDE_GAP for s in sizes) / len(sizes), 4)
        if sizes
        else None,
    }


#: Venues whose candles Kairos serves.
CANDLE_VENUES = ('kalshi', 'polymarket', 'predictfun', 'hyperliquid')


@dataclass
class PairHistory:
    """Both venues' trade prices for one relation, in the buckets where both traded.

    ``points`` holds ``(bucket start, market A price, market B price of the
    same outcome)``.
    """

    relation: MarketRelation
    start: datetime | None = None
    points: list[tuple[datetime, float, float]] = field(default_factory=list)
    problem: str = ''

    @property
    def gaps(self) -> list[float]:
        return [round(k - p, 6) for _, k, p in self.points]

    def summary(self) -> dict[str, Any]:
        a, b = self.relation.market_a, self.relation.market_b
        return {
            'relation_id': self.relation.relation_id,
            'venues': f"{a.get('venue')} + {b.get('venue')}",
            'market_a': a.get('name'),
            'market_b': b.get('name'),
            'start': self.start.isoformat() if self.start else None,
            **_stats(self.gaps),
            'problem': self.problem or None,
        }


def _sides(relation: MarketRelation) -> tuple[str, str, str, str, int] | None:
    """``(venue A, market A, venue B, market B, outcome of B)`` for a relation Kairos can chart."""
    a, b = relation.market_a, relation.market_b
    if a.get('venue') not in CANDLE_VENUES or b.get('venue') not in CANDLE_VENUES:
        return None
    if relation.spread_type not in ('same_event', 'complement'):
        return None
    if not a.get('market_id') or not b.get('market_id'):
        return None
    outcome = 0 if relation.spread_type == 'same_event' else 1
    return a['venue'], str(a['market_id']), b['venue'], str(b['market_id']), outcome


async def price_history(
    relations: Sequence[MarketRelation],
    client: KairosClient | None = None,
    *,
    timeframe: int = 60,
    before: timedelta = timedelta(hours=2),
    after: timedelta = timedelta(hours=4),
    now: datetime | None = None,
) -> list[PairHistory]:
    """Paired trade prices around each event's start, one result per relation.

    Args:
        relations: ``same_event`` or ``complement`` relations between any two
            venues Kairos charts, with ``game_start``, ``event_date`` or
            ``valid_until`` to place the event in time.
        timeframe: Candle width in seconds; Kairos keeps 1-minute candles for
            30 days.
        before: How long before the start to look.
        after: How long after the start to look.
    """
    client = client or KairosClient()
    now = now or datetime.now(timezone.utc)
    histories = [PairHistory(relation=r) for r in relations]
    wanted: list[tuple[PairHistory, CandleRequest, CandleRequest]] = []
    for history in histories:
        sides = _sides(history.relation)
        if sides is None:
            history.problem = 'not a same_event or complement relation Kairos can chart'
            continue
        start = event_time(history.relation)
        if start is None:
            history.problem = 'no start time'
            continue
        history.start = start
        window_start, window_end = start - before, min(start + after, now)
        if window_end <= window_start:
            history.problem = 'not started yet'
            continue
        venue_a, id_a, venue_b, id_b, outcome = sides
        wanted.append(
            (
                history,
                CandleRequest(venue_a, id_a, window_start, window_end, timeframe, 0),
                CandleRequest(
                    venue_b, id_b, window_start, window_end, timeframe, outcome
                ),
            )
        )
    await _fill(client, wanted)
    return histories


async def recent_history(
    relations: Sequence[MarketRelation],
    client: KairosClient | None = None,
    *,
    hours: float = 24,
    timeframe: int = 3600,
    before_event: bool = True,
    now: datetime | None = None,
) -> list[PairHistory]:
    """Paired trade prices over the last ``hours``, one result per relation.

    Args:
        relations: ``same_event`` or ``complement`` relations between any two
            venues Kairos charts.
        hours: Length of the window, ending now.
        timeframe: Candle width in seconds (one hour by default).
        before_event: Stop each pair's window at its event's start, so a game
            already under way is compared only on its pre-game prices.
    """
    client = client or KairosClient()
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=hours)
    histories = [PairHistory(relation=r) for r in relations]
    wanted: list[tuple[PairHistory, CandleRequest, CandleRequest]] = []
    for history in histories:
        sides = _sides(history.relation)
        if sides is None:
            history.problem = 'not a same_event or complement relation Kairos can chart'
            continue
        window_end = now
        start = event_time(history.relation)
        if before_event and start is not None and start < now:
            window_end = start
        if window_end <= window_start:
            history.problem = 'event started before the window'
            continue
        venue_a, id_a, venue_b, id_b, outcome = sides
        wanted.append(
            (
                history,
                CandleRequest(venue_a, id_a, window_start, window_end, timeframe, 0),
                CandleRequest(
                    venue_b, id_b, window_start, window_end, timeframe, outcome
                ),
            )
        )
    await _fill(client, wanted)
    return histories


async def _fill(
    client: KairosClient,
    wanted: Sequence[tuple[PairHistory, CandleRequest, CandleRequest]],
) -> None:
    """Fetch both sides' candles and keep the buckets where both venues traded."""
    series = await client.candles([req for _, k, p in wanted for req in (k, p)])
    for n, (history, _, _) in enumerate(wanted):
        first, second = series[2 * n], series[2 * n + 1]
        if first.error or second.error:
            history.problem = first.error or second.error
            continue
        a = {c.start: c.close for c in first.candles}
        b = {c.start: c.close for c in second.candles}
        history.points = sorted((t, a[t], b[t]) for t in a.keys() & b.keys())


def history_summary(
    histories: Sequence[PairHistory],
    *,
    bin_minutes: int = 30,
    first_minute: int = -120,
    last_minute: int = 240,
    widest: int = 10,
) -> dict[str, Any]:
    """Gap statistics over every pair, overall and by minutes from the start.

    Pairs without a start count in the overall statistics only.
    """
    all_gaps: list[float] = []
    before: list[float] = []
    after: list[float] = []
    bins: dict[int, list[float]] = {}
    for history in histories:
        for (moment, _, _), gap in zip(history.points, history.gaps, strict=True):
            all_gaps.append(gap)
            if history.start is None:
                continue
            minutes = (moment - history.start).total_seconds() / 60.0
            (before if minutes < 0 else after).append(gap)
            if first_minute <= minutes < last_minute:
                index = int((minutes - first_minute) // bin_minutes)
                bins.setdefault(index, []).append(gap)
    with_points = [h for h in histories if h.points]
    ranked = sorted(
        with_points, key=lambda h: max(abs(g) for g in h.gaps), reverse=True
    )
    problems = Counter(h.problem.split(':')[0] for h in histories if h.problem)
    return {
        'pairs': len(histories),
        'pairs_with_overlap': len(with_points),
        'problems': dict(problems.most_common()),
        **_stats(all_gaps),
        'before_start': _stats(before),
        'after_start': _stats(after),
        'by_minutes_from_start': [
            {
                'from': first_minute + i * bin_minutes,
                'to': first_minute + (i + 1) * bin_minutes,
                **_stats(bins.get(i, [])),
            }
            for i in range((last_minute - first_minute) // bin_minutes)
        ],
        'widest': [h.summary() for h in ranked[:widest]],
        'wide_gap': WIDE_GAP,
    }
