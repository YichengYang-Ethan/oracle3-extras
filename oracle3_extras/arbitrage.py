"""No-arbitrage scans over many relations, sized against the order books.

Mirrors :mod:`oracle3.arbitrage`. ``oracle3.arbitrage.check_constraint`` prices
one relation at the quotes it is given; :func:`scan_relations` runs it over many
relations at once. It fetches top-of-book quotes in batches
(:mod:`oracle3_extras.venues`), screens every relation, then re-checks the ones
with an edge after fees against full order books (:func:`walk_books`) to find
how many contracts the edge lasts for.

Relations are ``oracle3.market.MarketRelation`` objects whose two markets carry
``venue`` (``kalshi`` or ``polymarket``) and ``market_id`` (Kalshi ticker or
Polymarket market id), the same fields oracle3's ``check_constraint_live`` tool
takes. Everything here is read-only.

The checks inherit oracle3's assumptions: every leg fills at the prices used,
the relation is specified correctly (including matching resolution rules), and
capital locked until settlement costs nothing. Books are read one venue after
the other, so prices can move in between.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from oracle3.arbitrage import Basket, ConstraintCheck, Quote, check_constraint
from oracle3.fees import FeeSchedule, UnsupportedFeeSchedule
from oracle3.market.relations import MarketRelation

from oracle3_extras import venues
from oracle3_extras._http import APIError
from oracle3_extras.market.kairos.client import KairosClient
from oracle3_extras.market.kairos.markets import as_polymarket_shape

__all__ = [
    'SUPPORTED_RELATIONS',
    'DepthResult',
    'ScanItem',
    'ScanReport',
    'scan_relations',
    'walk_books',
]

#: Relation types that ``scan_relations`` can check for two markets.
SUPPORTED_RELATIONS = ('same_event', 'complement', 'implication', 'exclusivity')
#: Venues ``scan_relations`` can price: Kalshi and Polymarket from their own
#: APIs, Predict.fun through Kairos (last trades to screen, fee quotes to size).
VENUES = ('kalshi', 'polymarket', 'predictfun')
#: Basket sizes tried when a Predict.fun leg is priced with Kairos fee quotes.
QUOTE_SIZES = (10, 25, 50, 100, 250)

logger = logging.getLogger(__name__)


@dataclass
class DepthResult:
    """The best basket walked down the order books until its edge runs out.

    Attributes:
        contracts: Largest size with a positive net edge, within the cap.
        cost: Dollars paid for every leg at the prices walked.
        fees: Venue fees on every leg, charged level by level (a slight
            overestimate for Kalshi, which rounds once per order).
        net_edge: Guaranteed payoff minus cost minus fees.
        legs: Per leg: venue, market, side, contracts, average and worst price, fee.
        stopped_by: ``edge`` (the next level was unprofitable), ``book`` (a
            book ran out) or ``cap`` (``max_contracts`` reached).
    """

    basket: str
    contracts: float
    cost: float
    fees: float
    net_edge: float
    legs: list[dict[str, Any]]
    stopped_by: str

    @property
    def net_edge_per_contract(self) -> float:
        return round(self.net_edge / self.contracts, 6) if self.contracts else 0.0


@dataclass
class ScanItem:
    """One relation after the scan."""

    relation: MarketRelation
    check: ConstraintCheck | None = None
    depth: DepthResult | None = None
    skipped: str = ''
    #: Priced at the top of the book from last trades (Predict.fun), so only an
    #: order-book check can make it an opportunity.
    indicative: bool = False

    @property
    def best(self) -> Basket | None:
        return self.check.best if self.check else None

    @property
    def opportunity(self) -> bool:
        """An edge after fees that survived the order-book check, if one ran."""
        if self.skipped or self.check is None or not self.check.profitable_after_fees:
            return False
        if self.depth is None:
            return not self.indicative
        return self.depth.net_edge > 0

    def to_dict(self) -> dict[str, Any]:
        best = self.best
        a, b = self.relation.market_a, self.relation.market_b
        return {
            'relation_id': self.relation.relation_id,
            'relation': self.relation.spread_type,
            'market_a': {
                k: a.get(k) for k in ('venue', 'market_id', 'name', 'yes_outcome')
            },
            'market_b': {
                k: b.get(k) for k in ('venue', 'market_id', 'name', 'outcomes')
            },
            'basket': best.description if best else None,
            'top_of_book': {
                'contracts': best.contracts,
                'cost': best.cost,
                'fees': best.fees,
                'net_edge': best.net_edge,
                'net_edge_per_contract': best.net_edge_per_contract,
                'legs': [asdict(leg) for leg in best.legs],
            }
            if best
            else None,
            'depth': {
                **asdict(self.depth),
                'net_edge_per_contract': self.depth.net_edge_per_contract,
            }
            if self.depth
            else None,
            'warnings': list(self.relation.analysis_b.get('warnings') or []),
            'indicative': self.indicative,
            'skipped': self.skipped or None,
        }


@dataclass
class ScanReport:
    """Every relation scanned, and the ones with an edge after fees."""

    items: list[ScanItem]
    contracts: float
    maker: bool
    depth_checked: bool
    scanned_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec='seconds')
    )

    def opportunities(self) -> list[ScanItem]:
        """Opportunities, largest net edge first (sized by the books when checked)."""
        found = [item for item in self.items if item.opportunity]
        return sorted(
            found,
            key=lambda i: i.depth.net_edge if i.depth else i.best.net_edge,  # type: ignore[union-attr]
            reverse=True,
        )

    def summary(self) -> dict[str, Any]:
        checked = [i for i in self.items if i.check is not None]
        return {
            'scanned_at': self.scanned_at,
            'relations': len(self.items),
            'quoted': len(checked),
            'violated_before_fees': sum(1 for i in checked if i.check.violated),  # type: ignore[union-attr]
            'profitable_after_fees_top_of_book': sum(
                1
                for i in checked
                if i.check.profitable_after_fees  # type: ignore[union-attr]
            ),
            'opportunities': len(self.opportunities()),
            'priced_from_last_trades': sum(1 for i in checked if i.indicative),
            'skipped': dict(
                Counter(i.skipped.split(':')[0] for i in self.items if i.skipped)
            ),
            'contracts': self.contracts,
            'order_type': 'maker' if self.maker else 'taker',
            'depth_checked': self.depth_checked,
        }

    def to_dict(self, top: int | None = None) -> dict[str, Any]:
        found = self.opportunities()
        return {
            'summary': self.summary(),
            'opportunities': [i.to_dict() for i in (found[:top] if top else found)],
            'assumptions': [
                'every leg fills at the prices used',
                'both markets settle the same way, including ties, postponements and cancellations',
                'capital locked until settlement costs nothing',
                'books are read one venue after the other, so prices can move in between',
            ],
        }


# ── Depth ────────────────────────────────────────────────────────────────


def walk_books(
    basket: Basket,
    books: Mapping[tuple[str, str], venues.Book],
    schedules: Mapping[tuple[str, str], FeeSchedule],
    *,
    maker: bool = False,
    max_contracts: float | None = None,
    min_edge: float = 0.0,
) -> DepthResult | None:
    """Buy ``basket`` level by level while each level still pays after fees.

    Args:
        basket: A basket from ``check_constraint``; its legs name the markets
            and sides to buy and ``payoff_per_contract`` what it pays.
        books: Order books keyed by ``(venue, market_id)``.
        schedules: Fee schedules keyed by ``(venue, market_id)``.
        max_contracts: Stop at this size.
        min_edge: Smallest net edge per contract worth taking.

    Returns ``None`` when not even the first level is profitable.
    """
    legs = basket.legs
    ladders = [list(books[(leg.venue, leg.market_id)].asks(leg.side)) for leg in legs]
    rungs = [0] * len(legs)
    left = [ladder[0][1] if ladder else 0.0 for ladder in ladders]
    filled = [0.0] * len(legs)
    spent = [0.0] * len(legs)
    paid_fees = [0.0] * len(legs)
    worst = [0.0] * len(legs)
    total = 0.0
    stopped = 'book'
    while all(rung < len(ladder) for rung, ladder in zip(rungs, ladders, strict=True)):
        prices = [ladder[rung][0] for ladder, rung in zip(ladders, rungs, strict=True)]
        size = min(left)
        if max_contracts is not None:
            size = min(size, max_contracts - total)
            if size <= 1e-9:
                stopped = 'cap'
                break
        fees = [
            float(
                schedules[(leg.venue, leg.market_id)].fee(
                    Decimal(str(p)), Decimal(str(size)), maker=maker
                )
            )
            for leg, p in zip(legs, prices, strict=True)
        ]
        edge = basket.payoff_per_contract * size - sum(prices) * size - sum(fees)
        if edge <= min_edge * size:
            stopped = 'edge'
            break
        total += size
        for i, price in enumerate(prices):
            filled[i] += size
            spent[i] += price * size
            paid_fees[i] += fees[i]
            worst[i] = price
            left[i] -= size
            if left[i] <= 1e-9:
                rungs[i] += 1
                if rungs[i] < len(ladders[i]):
                    left[i] = ladders[i][rungs[i]][1]
    if total <= 0:
        return None
    cost = sum(spent)
    fees_total = sum(paid_fees)
    return DepthResult(
        basket=basket.description,
        contracts=round(total, 6),
        cost=round(cost, 6),
        fees=round(fees_total, 6),
        net_edge=round(basket.payoff_per_contract * total - cost - fees_total, 6),
        legs=[
            {
                'venue': leg.venue,
                'market_id': leg.market_id,
                'side': leg.side,
                'contracts': round(filled[i], 6),
                'average_price': round(spent[i] / filled[i], 6),
                'worst_price': worst[i],
                'fee': round(paid_fees[i], 6),
            }
            for i, leg in enumerate(legs)
        ],
        stopped_by=stopped,
    )


# ── Scan ─────────────────────────────────────────────────────────────────


_KNOWN_VENUES = (*VENUES, 'hyperliquid')


def _ref(market: Mapping[str, Any]) -> tuple[str, str] | None:
    venue = str(market.get('venue') or market.get('platform') or '').lower()
    market_id = str(market.get('market_id') or '')
    return (venue, market_id) if venue in _KNOWN_VENUES and market_id else None


async def _quotes(
    refs: Iterable[tuple[str, str]], client: KairosClient
) -> tuple[
    dict[tuple[str, str], Quote],
    dict[tuple[str, str], str],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    refs = set(refs)
    tickers = [m for v, m in refs if v == 'kalshi']
    poly_ids = [m for v, m in refs if v == 'polymarket']
    pf_ids = [m for v, m in refs if v == 'predictfun']
    unavailable: dict[str, str] = {}

    async def fetch(venue: str, job: Any, default: Any) -> Any:
        """One venue's data; a failure costs that venue's relations, not the scan."""
        if not job:
            return default
        try:
            return await job
        except APIError as exc:
            logger.warning('%s data unavailable: %s', venue, exc)
            unavailable[venue] = str(exc)
            return default

    started = time.monotonic()
    kalshi = await fetch('kalshi', tickers and venues.kalshi_markets(tickers), {})
    series = await fetch(
        'kalshi', kalshi and venues.kalshi_series({t.split('-')[0] for t in kalshi}), {}
    )
    poly = await fetch(
        'polymarket', poly_ids and venues.polymarket_markets(poly_ids), {}
    )
    found = await fetch(
        'predictfun', pf_ids and client.markets('predictfun', pf_ids), {}
    )
    predictfun = {m: as_polymarket_shape(v, 'predictfun') for m, v in found.items()}
    # One mark per market: the second outcome's book mirrors the first's, so its
    # price is taken as 1 minus the first (screening only).
    firsts = [
        (m, venues.polymarket_token_ids(v)[0])
        for m, v in predictfun.items()
        if venues.polymarket_token_ids(v)
    ]
    marks = await fetch('predictfun', firsts and client.marks('predictfun', firsts), {})
    logger.info('Quotes for %d markets in %.0fs', len(refs), time.monotonic() - started)
    quotes: dict[tuple[str, str], Quote] = {}
    problems: dict[tuple[str, str], str] = {}
    raw_by_venue = {'kalshi': kalshi, 'polymarket': poly, 'predictfun': predictfun}
    for venue, market_id in refs:
        raw = raw_by_venue.get(venue, {}).get(market_id)
        if raw is None:
            problems[(venue, market_id)] = (
                f'{venue} data unavailable'
                if venue in unavailable
                else f'{venue} market not found'
            )
            continue
        try:
            if venue == 'kalshi':
                quotes[(venue, market_id)] = venues.kalshi_quote(
                    raw, series.get(market_id.split('-')[0])
                )
            elif venue == 'polymarket':
                quotes[(venue, market_id)] = venues.polymarket_quote(raw)
            else:
                quote = venues.predictfun_quote(raw, marks)
                if quote.yes_ask is None and quote.no_ask is None:
                    problems[(venue, market_id)] = (
                        'predictfun data unavailable'
                        if venue in unavailable
                        else 'no Predict.fun trades yet'
                    )
                    continue
                quotes[(venue, market_id)] = quote
        except UnsupportedFeeSchedule as exc:
            problems[(venue, market_id)] = f'unsupported fee schedule: {exc}'
    return quotes, problems, poly, predictfun


async def _books(
    refs: Iterable[tuple[str, str]], poly: Mapping[str, Mapping[str, Any]]
) -> dict[tuple[str, str], venues.Book]:
    refs = set(refs)
    kalshi = await venues.kalshi_books(m for v, m in refs if v == 'kalshi')
    tokens = {
        m: venues.polymarket_token_ids(poly[m])
        for v, m in refs
        if v == 'polymarket' and m in poly
    }
    asks = await venues.polymarket_books(t for pair in tokens.values() for t in pair)
    books: dict[tuple[str, str], venues.Book] = {
        ('kalshi', ticker): book for ticker, book in kalshi.items()
    }
    for market_id, pair in tokens.items():
        if len(pair) == 2 and pair[0] in asks and pair[1] in asks:
            books[('polymarket', market_id)] = venues.polymarket_book(
                market_id, asks[pair[0]], asks[pair[1]]
            )
    return books


def _book_cost(
    asks: Sequence[venues.Level], size: float, schedule: FeeSchedule, maker: bool
) -> tuple[float, float, float] | None:
    """Cost, fee and worst price of buying ``size`` from ``asks``; ``None`` if too thin."""
    left, cost, fee, worst = size, 0.0, 0.0, 0.0
    for price, available in asks:
        take = min(left, available)
        cost += price * take
        fee += float(schedule.fee(Decimal(str(price)), Decimal(str(take)), maker=maker))
        worst, left = price, left - take
        if left <= 1e-9:
            return cost, fee, worst
    return None


async def _quoted_depth(
    basket: Basket,
    books: Mapping[tuple[str, str], venues.Book],
    schedules: Mapping[tuple[str, str], FeeSchedule],
    predictfun: Mapping[str, Mapping[str, Any]],
    client: KairosClient,
    *,
    maker: bool,
    max_contracts: float | None,
    min_edge: float,
) -> DepthResult | None:
    """Size a basket with a Predict.fun leg: Kairos fee quotes for that leg, books for the rest."""
    best: DepthResult | None = None
    stopped = 'sizes'
    for size in QUOTE_SIZES:
        if max_contracts is not None and size > max_contracts:
            stopped = 'cap'
            break
        legs: list[dict[str, Any]] = []
        for leg in basket.legs:
            if leg.venue == 'predictfun':
                tokens = venues.polymarket_token_ids(predictfun.get(leg.market_id, {}))
                token = (
                    tokens[0 if leg.side == 'yes' else 1] if len(tokens) == 2 else ''
                )
                quote = await client.fee_quote(
                    'predictfun', token_id=token, market_id=leg.market_id, quantity=size
                )
                if quote.get('unavailable') or not quote.get('sufficient_liquidity'):
                    legs = []
                    break
                cost = quote['avg_price'] * size
                legs.append(
                    {
                        'venue': leg.venue,
                        'market_id': leg.market_id,
                        'side': leg.side,
                        'contracts': size,
                        'average_price': round(quote['avg_price'], 6),
                        'worst_price': None,
                        'fee': round(quote['exchange_fee'], 6),
                        '_cost': cost,
                    }
                )
            else:
                book = books.get((leg.venue, leg.market_id))
                priced = (
                    _book_cost(
                        book.asks(leg.side),
                        size,
                        schedules[(leg.venue, leg.market_id)],
                        maker,
                    )
                    if book
                    else None
                )
                if priced is None:
                    legs = []
                    break
                cost, fee, worst = priced
                legs.append(
                    {
                        'venue': leg.venue,
                        'market_id': leg.market_id,
                        'side': leg.side,
                        'contracts': size,
                        'average_price': round(cost / size, 6),
                        'worst_price': worst,
                        'fee': round(fee, 6),
                        '_cost': cost,
                    }
                )
        if not legs:
            stopped = 'book'
            break
        cost = sum(leg.pop('_cost') for leg in legs)
        fees = sum(leg['fee'] for leg in legs)
        net = basket.payoff_per_contract * size - cost - fees
        if net <= min_edge * size:
            stopped = 'edge'
            break
        best = DepthResult(
            basket.description,
            float(size),
            round(cost, 6),
            round(fees, 6),
            round(net, 6),
            legs,
            'sizes',
        )
    if best is not None:
        best.stopped_by = stopped
    return best


async def scan_relations(
    relations: Sequence[MarketRelation],
    *,
    contracts: float = 1.0,
    maker: bool = False,
    depth: bool = True,
    max_contracts: float | None = None,
    min_edge: float = 0.0,
    client: KairosClient | None = None,
    max_confirm: int = 50,
) -> ScanReport:
    """Check every relation against live prices and size the survivors.

    Args:
        relations: Relations between Kalshi, Polymarket and Predict.fun markets
            (any source: Kairos, oracle3's relation store, or hand-written).
            Predict.fun legs are screened on last trades and sized with Kairos
            fee quotes, which need a Kairos API key; without one they stay
            unconfirmed and are never reported as opportunities.
        contracts: Size of the top-of-book check on every leg.
        maker: Price fees as resting orders instead of taker orders.
        depth: Re-check relations with an edge after fees against full order books.
        max_contracts: Cap for the order-book walk.
        min_edge: Smallest net edge per contract that counts.
        client: Kairos client for Predict.fun data; a default one if omitted.
        max_confirm: Most relations priced from last trades to confirm with
            Kairos fee quotes, largest edge first. Last trades can be weeks
            old, so most of these edges are stale; the rest stay unconfirmed.
    """
    client = client or KairosClient()
    items = [ScanItem(relation=r) for r in relations]
    refs: dict[int, list[tuple[str, str]]] = {}
    for n, item in enumerate(items):
        r = item.relation
        pair = [_ref(r.market_a), _ref(r.market_b)]
        if r.spread_type not in SUPPORTED_RELATIONS:
            item.skipped = f'relation type {r.spread_type} is not checked'
        elif None in pair:
            item.skipped = 'markets need venue and market_id'
        elif any(ref[0] not in VENUES for ref in pair):  # type: ignore[index]
            item.skipped = 'no live quotes for Hyperliquid yet'
        else:
            refs[n] = pair  # type: ignore[assignment]
    quotes, problems, poly, predictfun = await _quotes(
        (ref for pair in refs.values() for ref in pair), client
    )

    for n, pair in refs.items():
        item = items[n]
        missing = [problems[ref] for ref in pair if ref not in quotes]
        if missing:
            item.skipped = missing[0]
            continue
        item.check = check_constraint(
            item.relation.spread_type,
            [quotes[ref] for ref in pair],
            contracts=contracts,
            maker=maker,
        )
        item.indicative = any(venue == 'predictfun' for venue, _ in pair)

    candidates = [
        i
        for i in items
        if i.best is not None and i.best.net_edge_per_contract > min_edge
    ]
    logger.info(
        'Scanned %d relations: %d quoted, %d with an edge after fees at the top of the book',
        len(items),
        sum(1 for i in items if i.check),
        len(candidates),
    )
    if depth and candidates:
        indicative = sorted(
            (i for i in candidates if i.indicative),
            key=lambda i: i.best.net_edge,  # type: ignore[union-attr]
            reverse=True,
        )
        confirm = indicative[:max_confirm] if client.credentials else []
        for item in indicative[len(confirm) :]:
            item.skipped = (
                'unconfirmed: Predict.fun legs need a Kairos API key'
                if client.credentials is None
                else f'unconfirmed: beyond the {max_confirm} largest last-trade edges'
            )
        candidates = [i for i in candidates if not i.indicative] + confirm
        wanted = {
            (leg.venue, leg.market_id)
            for i in candidates
            for leg in i.best.legs  # type: ignore[union-attr]
            if leg.venue != 'predictfun'
        }
        try:
            books = await _books(wanted, poly)
        except APIError as exc:
            logger.warning('Could not read order books: %s', exc)
            books = {}
        schedules = {ref: quote.fee_schedule() for ref, quote in quotes.items()}
        for item in candidates:
            basket = item.best
            if basket is None:
                continue
            if item.indicative:
                try:
                    item.depth = await _quoted_depth(
                        basket,
                        books,
                        schedules,
                        predictfun,
                        client,
                        maker=maker,
                        max_contracts=max_contracts,
                        min_edge=min_edge,
                    ) or DepthResult(basket.description, 0.0, 0.0, 0.0, 0.0, [], 'edge')
                except APIError as exc:
                    item.skipped = (
                        f'unconfirmed: Kairos fee quote failed ({exc.status})'
                    )
                continue
            if any((leg.venue, leg.market_id) not in books for leg in basket.legs):
                item.skipped = 'order book unavailable'
                continue
            item.depth = walk_books(
                basket,
                books,
                schedules,
                maker=maker,
                max_contracts=max_contracts,
                min_edge=min_edge,
            ) or DepthResult(basket.description, 0.0, 0.0, 0.0, 0.0, [], 'edge')
    return ScanReport(
        items=items, contracts=contracts, maker=maker, depth_checked=depth
    )
