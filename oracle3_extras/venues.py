"""Batched, read-only market data from the public Kalshi and Polymarket APIs,
and Predict.fun's fee schedule.

``oracle3.mcp_server.venues`` reads one market per call, which suits an agent
checking one relation. Scanning thousands of relations needs batches: Kalshi's
``/markets`` and ``/markets/orderbooks`` take lists of tickers, Polymarket's
Gamma API takes lists of market ids and its CLOB returns many order books per
``POST /books``. Nothing here needs credentials or can place an order.

Prices are dollars per contract on both venues. For Polymarket, ``yes`` is a
market's first outcome token and ``no`` its second, as in oracle3.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

from oracle3.arbitrage import Quote
from oracle3.fees import KalshiSchedule, PolymarketSchedule

from oracle3_extras._http import chunks, gather_limited, new_client, request_json

__all__ = [
    'PREDICTFUN_DEFAULT_FEE_BPS',
    'PredictFunSchedule',
    'predictfun_quote',
    'CLOB_API',
    'GAMMA_API',
    'KALSHI_API',
    'Book',
    'Level',
    'kalshi_book',
    'kalshi_books',
    'kalshi_markets',
    'kalshi_quote',
    'kalshi_series',
    'polymarket_book',
    'polymarket_books',
    'polymarket_markets',
    'polymarket_outcomes',
    'polymarket_quote',
    'polymarket_token_ids',
]

KALSHI_API = 'https://api.elections.kalshi.com/trade-api/v2'
GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_API = 'https://clob.polymarket.com'

# Largest batches each endpoint accepted when tested (Gamma rejects 150 ids).
KALSHI_BATCH = 100
KALSHI_BOOK_BATCH = 50
GAMMA_BATCH = 50
CLOB_BATCH = 50

# Requests in flight per venue. Kalshi answers 429 above roughly 20 requests a
# second from one address; Polymarket's limits are looser.
KALSHI_CONCURRENCY = 2
POLYMARKET_CONCURRENCY = 4

Level = tuple[float, float]
"""One order-book level: (price in dollars, size in contracts)."""


@dataclass
class Book:
    """Asks for buying each side of one binary contract, best price first."""

    venue: str
    market_id: str
    yes_asks: list[Level] = field(default_factory=list)
    no_asks: list[Level] = field(default_factory=list)

    def asks(self, side: str) -> list[Level]:
        return self.yes_asks if side == 'yes' else self.no_asks


def _price(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if 0.0 < x < 1.0 else None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return list(value) if isinstance(value, list) else []


# ── Kalshi ───────────────────────────────────────────────────────────────


async def kalshi_markets(tickers: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Market objects keyed by ticker; unknown tickers are left out."""
    unique = list(dict.fromkeys(t for t in tickers if t))
    async with new_client() as client:

        async def fetch(batch: Any) -> list[dict[str, Any]]:
            data = await request_json(
                client,
                'GET',
                f'{KALSHI_API}/markets',
                params={'tickers': ','.join(batch), 'limit': len(batch)},
            )
            return data.get('markets') or []

        pages = await gather_limited(
            (fetch(b) for b in chunks(unique, KALSHI_BATCH)), KALSHI_CONCURRENCY
        )
    return {m['ticker']: m for page in pages for m in page if m.get('ticker')}


async def kalshi_series(series_tickers: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Series objects (with ``fee_type`` and ``fee_multiplier``) keyed by ticker."""
    unique = list(dict.fromkeys(t for t in series_tickers if t))
    async with new_client() as client:

        async def fetch(ticker: str) -> dict[str, Any]:
            data = await request_json(client, 'GET', f'{KALSHI_API}/series/{ticker}')
            return data.get('series') or {}

        found = await gather_limited((fetch(t) for t in unique), KALSHI_CONCURRENCY)
    return {t: s for t, s in zip(unique, found, strict=True) if s}


def kalshi_book(ticker: str, orderbook: Mapping[str, Any]) -> Book:
    """Convert Kalshi's bid-only book into asks for each side.

    Kalshi lists bids only: a YES ask at ``p`` is a NO bid at ``1 - p``.
    """
    book = orderbook.get('orderbook_fp') or orderbook
    yes_bids = _levels(book.get('yes_dollars'))
    no_bids = _levels(book.get('no_dollars'))
    return Book(
        venue='kalshi',
        market_id=ticker,
        yes_asks=sorted((round(1.0 - p, 6), q) for p, q in no_bids),
        no_asks=sorted((round(1.0 - p, 6), q) for p, q in yes_bids),
    )


async def kalshi_books(tickers: Iterable[str]) -> dict[str, Book]:
    unique = list(dict.fromkeys(t for t in tickers if t))
    async with new_client() as client:

        async def fetch(batch: Any) -> list[dict[str, Any]]:
            data = await request_json(
                client,
                'GET',
                f'{KALSHI_API}/markets/orderbooks',
                params={'tickers': list(batch)},
            )
            return data.get('orderbooks') or []

        pages = await gather_limited(
            (fetch(b) for b in chunks(unique, KALSHI_BOOK_BATCH)), KALSHI_CONCURRENCY
        )
    return {
        row['ticker']: kalshi_book(row['ticker'], row)
        for page in pages
        for row in page
        if row.get('ticker') in unique
    }


def kalshi_quote(
    market: Mapping[str, Any], series: Mapping[str, Any] | None = None
) -> Quote:
    """Top-of-book quote with the series fee schedule.

    Raises ``oracle3.fees.UnsupportedFeeSchedule`` for series oracle3 cannot
    price. Without ``series`` the standard schedule is assumed.
    """
    return Quote(
        market_id=str(market.get('ticker', '')),
        venue='kalshi',
        yes_bid=_price(market.get('yes_bid_dollars')),
        yes_ask=_price(market.get('yes_ask_dollars')),
        no_bid=_price(market.get('no_bid_dollars')),
        no_ask=_price(market.get('no_ask_dollars')),
        schedule=KalshiSchedule.from_series(series) if series else KalshiSchedule(),
        title=str(market.get('title', '')),
    )


# ── Polymarket ───────────────────────────────────────────────────────────


async def polymarket_markets(
    ids: Iterable[str], *, include_closed: bool = False
) -> dict[str, dict[str, Any]]:
    """Gamma market objects keyed by market id (as a string).

    Gamma leaves closed markets out of a lookup by id unless asked with
    ``closed=true``, which in turn returns only closed ones; with
    ``include_closed`` the ids not found open are looked up again as closed.
    """
    unique = list(dict.fromkeys(str(i) for i in ids if i))
    found = await _gamma_lookup(unique, closed=False)
    missing = [i for i in unique if i not in found]
    if include_closed and missing:
        found.update(await _gamma_lookup(missing, closed=True))
    return found


async def _gamma_lookup(ids: list[str], *, closed: bool) -> dict[str, dict[str, Any]]:
    async with new_client() as client:

        async def fetch(batch: Any) -> list[dict[str, Any]]:
            params = [('id', i) for i in batch] + [('limit', str(len(batch)))]
            if closed:
                params.append(('closed', 'true'))
            data = await request_json(
                client, 'GET', f'{GAMMA_API}/markets', params=params
            )
            return data if isinstance(data, list) else []

        pages = await gather_limited(
            (fetch(b) for b in chunks(ids, GAMMA_BATCH)), POLYMARKET_CONCURRENCY
        )
    return {str(m['id']): m for page in pages for m in page if m.get('id') is not None}


def polymarket_outcomes(market: Mapping[str, Any]) -> list[str]:
    return [str(o) for o in _json_list(market.get('outcomes'))]


def polymarket_token_ids(market: Mapping[str, Any]) -> list[str]:
    return [str(t) for t in _json_list(market.get('clobTokenIds'))]


def polymarket_quote(market: Mapping[str, Any]) -> Quote:
    """Top-of-book quote for the first outcome token, from Gamma's best bid and ask.

    The two outcome books of a Polymarket market mirror each other, so the
    second token's ask is ``1 - best bid``, which ``Quote.ask`` derives.
    Raises ``oracle3.fees.UnsupportedFeeSchedule`` for undocumented schedules.
    """
    return Quote(
        market_id=str(market.get('id', '')),
        venue='polymarket',
        yes_bid=_price(market.get('bestBid')),
        yes_ask=_price(market.get('bestAsk')),
        schedule=PolymarketSchedule.from_gamma_market(market),
        title=str(market.get('question', '')),
    )


def polymarket_book(market_id: str, first_asks: Any, second_asks: Any) -> Book:
    return Book(
        venue='polymarket',
        market_id=str(market_id),
        yes_asks=sorted(_levels(first_asks)),
        no_asks=sorted(_levels(second_asks)),
    )


async def polymarket_books(token_ids: Iterable[str]) -> dict[str, list[Level]]:
    """Asks for each outcome token, best price first, keyed by token id."""
    unique = list(dict.fromkeys(t for t in token_ids if t))
    async with new_client() as client:

        async def fetch(batch: Any) -> list[dict[str, Any]]:
            data = await request_json(
                client,
                'POST',
                f'{CLOB_API}/books',
                json=[{'token_id': t} for t in batch],
            )
            return data if isinstance(data, list) else []

        pages = await gather_limited(
            (fetch(b) for b in chunks(unique, CLOB_BATCH)), POLYMARKET_CONCURRENCY
        )
    return {
        str(b['asset_id']): sorted(_levels(b.get('asks')))
        for page in pages
        for b in page
        if b.get('asset_id')
    }


def _levels(raw: Any) -> list[Level]:
    out: list[Level] = []
    for level in raw or []:
        if isinstance(level, Mapping):
            price, size = level.get('price'), level.get('size')
        else:
            price, size = level[0], level[1]
        try:
            p, q = float(price), float(size)
        except (TypeError, ValueError):
            continue
        if 0.0 < p < 1.0 and q > 0:
            out.append((p, q))
    return out


# ── Predict.fun (through Kairos) ─────────────────────────────────────────

#: Taker fee Kairos assumes when a Predict.fun market publishes none (2%).
PREDICTFUN_DEFAULT_FEE_BPS = 200


@dataclass(frozen=True)
class PredictFunSchedule:
    """Predict.fun taker fee: ``rate × min(p, 1 − p) × shares``; makers pay nothing.

    Published in Kairos's fee documentation; ``rate_bps`` comes from each
    market's metadata. It plugs into ``oracle3.arbitrage.check_constraint``
    like oracle3's own Kalshi and Polymarket schedules.
    """

    rate_bps: int = PREDICTFUN_DEFAULT_FEE_BPS

    venue: ClassVar[str] = 'predictfun'

    def fee(
        self, price: float | Decimal, contracts: float | Decimal, *, maker: bool = False
    ) -> Decimal:
        p = Decimal(str(price))
        if not Decimal('0') < p < Decimal('1'):
            raise ValueError(f'price must be strictly between 0 and 1, got {price}')
        if maker:
            return Decimal('0')
        raw = (
            Decimal(self.rate_bps)
            / Decimal(10_000)
            * min(p, 1 - p)
            * Decimal(str(contracts))
        )
        return raw.quantize(Decimal('0.000001'))

    def describe(self) -> dict[str, Any]:
        return {
            'venue': self.venue,
            'formula': 'rate * min(p, 1 - p) * shares, takers only',
            'taker_rate_bps': self.rate_bps,
        }


def predictfun_quote(
    market: Mapping[str, Any], marks: Mapping[tuple[str, str], float]
) -> Quote:
    """An indicative quote from each outcome's last trade, for screening only.

    ``market`` is Kairos metadata in Polymarket shape
    (:func:`oracle3_extras.market.kairos.markets.as_polymarket_shape`).
    """
    market_id = str(market.get('id') or '')
    tokens = polymarket_token_ids(market)
    first = marks.get((market_id, tokens[0])) if tokens else None
    second = marks.get((market_id, tokens[1])) if len(tokens) > 1 else None
    if second is None and first is not None:
        second = round(1.0 - first, 6)
    rate = market.get('feeRateBps')
    return Quote(
        market_id=market_id,
        venue='predictfun',
        yes_ask=_price(first),
        no_ask=_price(second),
        schedule=PredictFunSchedule(int(rate) if rate else PREDICTFUN_DEFAULT_FEE_BPS),  # type: ignore[arg-type]
        title=str(market.get('question') or ''),
    )
