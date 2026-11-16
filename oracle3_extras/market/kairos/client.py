"""Client for Kairos's public Data API and Market Data API.

Kairos links the same contract across Kalshi, Polymarket, Predict.fun and
Hyperliquid and publishes the approved links as a catalog
(``https://data.kairos.trade/market-links/featured``). Its Market Data API
(``https://md.kairos.trade``) serves normalized candles and settlement results
for every venue it covers. The endpoints used here need no account; an API key
raises the rate limits (``KAIROS_CLIENT_ID``, ``KAIROS_API_KEY`` and
``KAIROS_API_SECRET``).

Requests identify themselves with the oracle3-extras User-Agent and nothing
else about the user.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from oracle3_extras import _http
from oracle3_extras._http import (
    APIError,
    chunks,
    gather_limited,
    new_client,
    request_json,
)

__all__ = [
    'DATA_API',
    'EXECUTION_API',
    'MARKET_DATA_API',
    'MIN_SIMILARITY',
    'PROVIDER_IDS',
    'Candle',
    'CandleRequest',
    'CandleSeries',
    'KairosClient',
    'KairosCredentials',
    'KairosMarket',
    'MatchedMarkets',
    'MatchedPair',
]

DATA_API = 'https://data.kairos.trade'
MARKET_DATA_API = 'https://md.kairos.trade'
EXECUTION_API = 'https://execution.kairos.trade'

#: Candle widths the Market Data API serves, in seconds.
TIMEFRAMES = (1, 60, 300, 900, 3600, 14400, 86400)

#: Floor of Kairos's retired similarity catalog (``GET /matched-markets``).
#: Approved links carry no similarity; they come in at 1.0.
MIN_SIMILARITY = 0.82

#: Kairos's numeric provider ids (``numeric_id`` in ``GET /providers/configs``).
PROVIDER_IDS = {'kalshi': 1, 'polymarket': 2, 'predictfun': 8, 'hyperliquid': 9}

#: Links per ``/market-links/featured`` page, Kairos's maximum.
LINKS_PAGE_SIZE = 100
#: Markets per ``POST /markets/details`` call, Kairos's hard cap.
DETAILS_BATCH = 500

logger = logging.getLogger(__name__)


#: Requested buckets per ``/v1/candles/batch`` call (25 six-hour series of
#: one-minute candles); Kairos also charges a light unit per 5,000.
CANDLE_BARS_PER_CALL = 9_000

#: How long Kairos keeps candles of each width, in days.
CANDLE_RETENTION_DAYS = {
    1: 30,
    60: 30,
    300: 30,
    900: 30,
    3600: 365,
    14400: 365,
    86400: 730,
}

#: Pairs per ``/v1/marks`` call: each started 100 costs one heavy unit.
MARKS_BATCH = 100
#: Characters of ``pairs`` per call: a 16,068-character URL was accepted, ~17,800 refused (HTTP 414).
MARKS_MAX_CHARS = 15_000


def _url_batches(
    pairs: Sequence[tuple[str, str]], size: int, max_chars: int
) -> list[list[tuple[str, str]]]:
    """``contract:token`` pairs in batches short enough for one query string."""
    batches: list[list[tuple[str, str]]] = []
    batch: list[tuple[str, str]] = []
    used = 0
    for pair in pairs:
        width = len(pair[0]) + len(pair[1]) + 6  # ':' and ',' are sent as %3A, %2C
        if batch and (len(batch) >= size or used + width > max_chars):
            batches.append(batch)
            batch, used = [], 0
        batch.append(pair)
        used += width
    if batch:
        batches.append(batch)
    return batches


def _bars(request: CandleRequest) -> int:
    seconds = (request.end - request.start).total_seconds()
    keep = CANDLE_RETENTION_DAYS.get(request.timeframe, 30) * 86400
    return max(1, math.ceil(min(seconds, keep) / request.timeframe))


def _candle_batches(
    results: Sequence[CandleSeries], size: int, max_bars: int
) -> list[list[CandleSeries]]:
    """Series in request order, in batches of at most ``size`` series and ``max_bars`` buckets."""
    batches: list[list[CandleSeries]] = []
    batch: list[CandleSeries] = []
    used = 0
    for series in results:
        bars = _bars(series.request)
        if batch and (len(batch) >= size or used + bars > max_bars):
            batches.append(batch)
            batch, used = [], 0
        batch.append(series)
        used += bars
    if batch:
        batches.append(batch)
    return batches


@dataclass(frozen=True)
class KairosMarket:
    """One side of a matched pair, in the venue's own identifiers.

    ``market_id`` is the Kalshi ticker, the numeric Polymarket (Gamma) market
    id, the Predict.fun market id or the Hyperliquid outcome id. For Polymarket,
    ``ticker`` holds the market's UMA question id (Gamma ``questionID``), not
    its condition id.
    """

    provider: str
    market_id: str
    provider_id: int | None = None
    title: str | None = None
    ticker: str | None = None
    expires_at: str | None = None
    category: str | None = None

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> KairosMarket:
        return cls(
            provider=str(raw.get('provider') or '').lower(),
            market_id=str(raw.get('market_id') or ''),
            provider_id=raw.get('provider_id'),
            title=raw.get('title'),
            ticker=raw.get('ticker'),
            expires_at=raw.get('expires_at'),
            category=raw.get('category'),
        )


@dataclass(frozen=True)
class MatchedPair:
    """Two markets that Kairos matched as the same underlying question.

    Pairs from an approved Kairos link carry its ``link_id`` and a similarity
    of 1.0: the link is Kairos's own verdict that both markets are one
    contract. Pairs from the retired ``/matched-markets`` catalog kept its
    embedding similarity (0.82 to 1).
    """

    a: KairosMarket
    b: KairosMarket
    similarity: float
    updated_at: str = ''
    link_id: str = ''

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> MatchedPair:
        return cls(
            a=KairosMarket.from_api(raw.get('a') or {}),
            b=KairosMarket.from_api(raw.get('b') or {}),
            similarity=float(raw.get('similarity') or 0.0),
            updated_at=str(raw.get('updated_at') or ''),
        )

    @property
    def providers(self) -> frozenset[str]:
        return frozenset({self.a.provider, self.b.provider})

    @property
    def key(self) -> tuple[tuple[str, str], ...]:
        """Identity that ignores which side Kairos listed first."""
        return tuple(
            sorted(
                (
                    (self.a.provider, self.a.market_id),
                    (self.b.provider, self.b.market_id),
                )
            )
        )

    def side(self, provider: str) -> KairosMarket | None:
        for market in (self.a, self.b):
            if market.provider == provider:
                return market
        return None


@dataclass
class MatchedMarkets:
    """The matched-pair catalog as one consistent snapshot."""

    pairs: list[MatchedPair]
    catalog_version: str = ''
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec='seconds')
    )

    def between(self, first: str, second: str) -> list[MatchedPair]:
        """Pairs with one side on each of the two venues."""
        wanted = frozenset({first, second})
        return [p for p in self.pairs if p.providers == wanted]


class _StaleCursor(Exception):
    """A catalog page cursor stopped working mid-walk; the walk restarts."""

    def __init__(self, error: APIError) -> None:
        super().__init__(str(error))
        self.error = error


def _leg_market_id(provider: str, leg: Mapping[str, Any]) -> str:
    """A link leg's market id, in the form the rest of this package uses.

    Polymarket markets are keyed by their numeric Gamma id, which Kairos
    publishes as the leg's ``streamKey`` (its ``marketId`` is the condition
    id). The other venues use ``marketId``: the Kalshi ticker, the Predict.fun
    market id and the Hyperliquid outcome id.
    """
    if provider == 'polymarket':
        stream = str(leg.get('streamKey') or '')
        if stream.isdigit():
            return stream
    return str(leg.get('marketId') or '')


def _link_pairs(
    link: Mapping[str, Any], details: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[MatchedPair]:
    """Every two venues of one approved Kairos link, as :class:`MatchedPair`.

    ``details`` maps ``(provider, market id)`` to Kairos's market details,
    which give each market its own name and category; a market missing from
    them keeps the link's title and no category.
    """
    link_id = str(link.get('id') or '')
    markets: list[KairosMarket] = []
    for leg in link.get('venues') or []:
        provider = str(leg.get('provider') or '').lower()
        market_id = _leg_market_id(provider, leg)
        if not provider or not market_id:
            continue
        if any(m.provider == provider for m in markets):
            continue  # one market per venue
        info = details.get((provider, market_id)) or {}
        markets.append(
            KairosMarket(
                provider=provider,
                market_id=market_id,
                provider_id=PROVIDER_IDS.get(provider),
                title=info.get('name') or link.get('title'),
                ticker=market_id if provider == 'kalshi' else None,
                expires_at=link.get('endTime'),
                category=info.get('category'),
            )
        )
    return [
        MatchedPair(a=a, b=b, similarity=1.0, link_id=link_id)
        for i, a in enumerate(markets)
        for b in markets[i + 1 :]
    ]


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@dataclass(frozen=True)
class CandleRequest:
    """One candle series: a market's outcome over ``[start, end)``.

    ``contract_id`` is the Kalshi ticker or the numeric Polymarket market id.
    ``outcome`` is the outcome index: 0 is YES on Kalshi and the first outcome
    token on Polymarket.
    """

    provider: str
    contract_id: str
    start: datetime
    end: datetime
    timeframe: int = 60
    outcome: int = 0

    def __post_init__(self) -> None:
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f'timeframe must be one of {TIMEFRAMES} seconds')
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError('candle windows need timezone-aware datetimes')

    def to_api(self) -> dict[str, Any]:
        return {
            'provider': self.provider,
            'contract_id': self.contract_id,
            'timeframe_seconds': self.timeframe,
            'start': _rfc3339(self.start),
            'end': _rfc3339(self.end),
            'outcome': self.outcome,
        }


@dataclass(frozen=True)
class Candle:
    """One bucket of trades. Prices are dollars (0 to 1); Kairos sends 0 to 100."""

    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> Candle:
        def price(key: str) -> float:
            return round(float(raw[key]) / 100.0, 6)

        return cls(
            start=datetime.fromisoformat(
                str(raw['bucket_start']).replace('Z', '+00:00')
            ),
            open=price('open'),
            high=price('high'),
            low=price('low'),
            close=price('close'),
            volume=float(raw.get('volume') or 0),
        )


@dataclass
class CandleSeries:
    """Candles for one request, oldest first. Buckets without trades are absent."""

    request: CandleRequest
    candles: list[Candle] = field(default_factory=list)
    error: str = ''


@dataclass(frozen=True)
class KairosCredentials:
    """An optional Kairos API key; anonymous access works with lower limits."""

    client_id: str
    api_key: str
    api_secret: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> KairosCredentials | None:
        env = os.environ if env is None else env
        values = [
            env.get(k, '')
            for k in ('KAIROS_CLIENT_ID', 'KAIROS_API_KEY', 'KAIROS_API_SECRET')
        ]
        return cls(*values) if all(values) else None

    def headers(self) -> dict[str, str]:
        return {
            'X-Client-Id': self.client_id,
            'X-Api-Key': self.api_key,
            'X-Api-Secret': self.api_secret,
        }

    def __repr__(self) -> str:  # never print the secrets
        return f'KairosCredentials(client_id={self.client_id!r}, api_key=***, api_secret=***)'


class KairosClient:
    """Read-only access to Kairos's cross-venue matching data.

    Args:
        credentials: API key for higher rate limits. Defaults to the
            ``KAIROS_*`` environment variables, and to anonymous access when
            they are not set.
        base_url: Data API root, for testing against another deployment.
        market_data_url: Market Data API root.
        execution_url: Execution API root (only its read-only fee quotes are used).
        page_size: Links per catalog page (Kairos serves at most 100, and
            anything larger is sent as 100).
        max_restarts: How many times to restart a catalog walk whose page
            cursor stops working mid-walk (HTTP 400, 409 or 410).
    """

    def __init__(
        self,
        *,
        credentials: KairosCredentials | None = None,
        base_url: str = DATA_API,
        market_data_url: str = MARKET_DATA_API,
        execution_url: str = EXECUTION_API,
        page_size: int = 1000,
        max_restarts: int = 3,
        timeout: float = 20.0,
    ) -> None:
        if not 1 <= page_size <= 1000:
            raise ValueError('page_size must be between 1 and 1000')
        self.credentials = (
            credentials if credentials is not None else KairosCredentials.from_env()
        )
        self.base_url = base_url.rstrip('/')
        self.market_data_url = market_data_url.rstrip('/')
        self.execution_url = execution_url.rstrip('/')
        self.page_size = page_size
        self.max_restarts = max_restarts
        self.timeout = timeout

    def _client(self):  # noqa: ANN202
        headers = self.credentials.headers() if self.credentials else None
        return new_client(timeout=self.timeout, headers=headers)

    async def matched_markets(
        self, *, provider: str | None = None, min_similarity: float | None = None
    ) -> MatchedMarkets:
        """The whole cross-venue catalog: every approved Kairos link, as pairs.

        Kairos retired its similarity catalog (``GET /matched-markets``) in
        November 2026. Its successor, ``GET /market-links/featured``, lists
        approved links: one contract listed on two to four venues. Every two
        venues of a link make one :class:`MatchedPair`, so a link on Kalshi,
        Polymarket and Predict.fun gives three. Each market's own name and
        Kairos category come from ``POST /markets/details``.

        Args:
            provider: Only pairs with a side on this venue (``kalshi``,
                ``polymarket``, ``predictfun`` or ``hyperliquid``).
            min_similarity: Ignored: links carry no similarity score. Kept so
                callers written for the old catalog keep working.

        Kairos rebuilds the list every minute. If a page cursor stops working
        mid-walk (HTTP 400, 409 or 410), the walk restarts.
        """
        if min_similarity is not None:
            logger.info('Kairos links carry no similarity; min_similarity is ignored')
        links = await self._links()
        details = await self.market_details(
            (venue, _leg_market_id(venue, leg))
            for link in links
            for leg in link.get('venues') or []
            if (venue := str(leg.get('provider') or '').lower())
        )
        pairs: list[MatchedPair] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for link in links:
            for pair in _link_pairs(link, details):
                if provider and provider not in pair.providers:
                    continue
                if pair.key not in seen:
                    seen.add(pair.key)
                    pairs.append(pair)
        logger.info('Kairos: %d links, %d pairs', len(links), len(pairs))
        return MatchedMarkets(pairs=pairs)

    async def _links(self) -> list[dict[str, Any]]:
        """Every approved link, restarting a walk whose cursor goes stale."""
        for attempt in range(self.max_restarts + 1):
            try:
                return await self._walk_links()
            except _StaleCursor as stale:
                if attempt == self.max_restarts:
                    raise stale.error from None
                logger.info('Kairos link list changed during the walk; restarting')
        raise AssertionError('unreachable')  # pragma: no cover

    async def _walk_links(self) -> list[dict[str, Any]]:
        params: dict[str, Any] = {'limit': min(self.page_size, LINKS_PAGE_SIZE)}
        links: list[dict[str, Any]] = []
        ids: set[str] = set()
        cursors: set[str] = set()
        async with self._client() as client:
            while True:
                try:
                    data = await request_json(
                        client,
                        'GET',
                        f'{self.base_url}/market-links/featured',
                        params=params,
                    )
                except APIError as exc:
                    if exc.status == 409 or (
                        'cursor' in params and exc.status in (400, 410)
                    ):
                        raise _StaleCursor(exc) from exc
                    raise
                for link in data.get('links') or []:
                    link_id = str(link.get('id') or '')
                    if link_id and link_id in ids:
                        continue
                    ids.add(link_id)
                    links.append(link)
                cursor = data.get('nextCursor')
                if not cursor or cursor in cursors:
                    break
                cursors.add(cursor)
                params['cursor'] = cursor
        return links

    async def market_details(
        self, markets: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Kairos's name, category and status for each ``(provider, market id)``.

        ``POST /markets/details``, 500 markets per call. A Polymarket market
        can be asked for by its numeric id or its condition id, and is found
        under both. Markets Kairos does not know are left out.
        """
        refs = list(
            dict.fromkeys(
                (provider, str(market_id))
                for provider, market_id in markets
                if provider in PROVIDER_IDS and market_id
            )
        )
        names = {number: name for name, number in PROVIDER_IDS.items()}
        found: dict[tuple[str, str], dict[str, Any]] = {}
        async with self._client() as client:
            for batch in chunks(refs, DETAILS_BATCH):
                data = await request_json(
                    client,
                    'POST',
                    f'{self.base_url}/markets/details',
                    json={
                        'markets': [
                            {'market_id': market_id, 'provider_id': PROVIDER_IDS[venue]}
                            for venue, market_id in batch
                        ]
                    },
                )
                for row in data.get('markets') or []:
                    venue = names.get(row.get('provider_id'))
                    if venue is None:
                        continue
                    for key in (row.get('market_id'), row.get('condition_id')):
                        if key:
                            found.setdefault((venue, str(key)), row)
        return found

    async def market_clusters(
        self, markets: Iterable[tuple[int, str]], *, floor: str = 'exact'
    ) -> dict[str, dict[str, Any]]:
        """Every live venue listing the same contract as each ``(provider_id, market_id)``.

        ``floor='exact'`` keeps listings of the same contract; ``'semantic'``
        adds Kairos's broader equivalent grouping. References without a
        cluster of at least two live markets are left out.
        """
        if floor not in ('exact', 'semantic'):
            raise ValueError("floor must be 'exact' or 'semantic'")
        refs = list(dict.fromkeys(f'{pid}:{mid}' for pid, mid in markets))
        found: dict[str, dict[str, Any]] = {}
        async with self._client() as client:
            for batch in chunks(refs, 200):
                data = await request_json(
                    client,
                    'GET',
                    f'{self.base_url}/market-clusters',
                    params={'markets': ','.join(batch), 'floor': floor},
                )
                found.update(data.get('clusters') or {})
        return found

    async def candles(
        self,
        requests: Sequence[CandleRequest],
        *,
        batch_size: int = 200,
        max_bars: int = CANDLE_BARS_PER_CALL,
        pause: float = 1.0,
    ) -> list[CandleSeries]:
        """Candle series in request order, from ``/v1/candles/batch``.

        Kairos accepts up to 200 series per call, but large one-minute batches
        are expensive for it to build (200 six-hour series of one-minute
        candles came back as HTTP 502). So a call carries at most
        ``batch_size`` series and ``max_bars`` requested buckets (window ÷
        timeframe, summed over the series), and calls are ``pause`` seconds
        apart. If Kairos still fails after the usual retries, the remaining
        series are marked with the error instead of sending more requests.

        Kairos keeps 1-minute candles for 30 days, 1-hour for 365 and daily for
        730; longer windows are clamped. Candles exist only for buckets with
        trades. A series Kairos rejects comes back with ``error`` set.
        """
        if not 1 <= batch_size <= 200:
            raise ValueError('batch_size must be between 1 and 200')
        results = [CandleSeries(request=r) for r in requests]
        batches = _candle_batches(results, batch_size, max_bars)
        async with self._client() as client:
            offset = 0
            for batch in batches:
                if offset:
                    await _http.pause(pause)
                try:
                    data = await request_json(
                        client,
                        'POST',
                        f'{self.market_data_url}/v1/candles/batch',
                        json={
                            'requests': [series.request.to_api() for series in batch]
                        },
                    )
                except APIError as exc:
                    logger.warning('Kairos candles failed; stopping: %s', exc)
                    for series in results[offset:]:
                        series.error = f'not fetched: {exc}'
                    break
                for row in data.get('results') or []:
                    index = row.get('index')
                    if not isinstance(index, int) or not 0 <= index < len(batch):
                        continue
                    series = batch[index]
                    series.error = str(row.get('error') or '')
                    series.candles = [
                        Candle.from_api(c) for c in row.get('candles') or []
                    ]
                offset += len(batch)
        return results

    async def resolutions(
        self, provider: str, market_ids: Iterable[str]
    ) -> dict[str, float]:
        """Settled YES fractions (0 to 1) for the markets that have resolved.

        Kalshi markets are looked up by ticker. Polymarket markets are looked up
        by their numeric market id: on 7 October 2026 Kairos resolved those, and
        nothing for condition ids, although its documentation says condition
        ids. For Polymarket the fraction is that of the market's first outcome.
        Unresolved markets are left out.
        """
        ids = list(dict.fromkeys(str(m) for m in market_ids if m))
        found: dict[str, float] = {}
        async with self._client() as client:
            for batch in chunks(ids, 200):
                data = await request_json(
                    client,
                    'GET',
                    f'{self.market_data_url}/v1/resolutions',
                    params={'provider': provider, 'market_ids': ','.join(batch)},
                )
                for market_id, fraction in (data.get('resolutions') or {}).items():
                    found[str(market_id)] = float(fraction)
        return found

    async def markets(
        self, provider: str, market_ids: Iterable[str]
    ) -> dict[str, dict[str, Any]]:
        """Kairos's metadata for each market id: title, outcomes with token ids, status.

        Works for every venue Kairos covers (``kalshi``, ``polymarket``,
        ``predictfun``, ``hyperliquid``), 200 ids per call to the public
        ``/v1/markets/batch``. Unknown ids are left out.
        """
        ids = list(dict.fromkeys(str(m) for m in market_ids if m))
        found: dict[str, dict[str, Any]] = {}
        async with self._client() as client:
            for batch in chunks(ids, 200):
                data = await request_json(
                    client,
                    'POST',
                    f'{self.market_data_url}/v1/markets/batch',
                    json={'provider': provider, 'market_ids': list(batch)},
                )
                for market_id, market in (data.get('markets') or {}).items():
                    found[str(market_id)] = market
        return found

    async def marks(
        self, provider: str, tokens: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], float]:
        """Last traded price (0 to 1) per ``(market id, outcome token id)``.

        Public ``/v1/marks``. Tokens that have never traded are left out. A
        last trade is not a quote: use it to screen, not to price a trade.

        The endpoint accepts 200 pairs, but 200 Predict.fun or Polymarket pairs
        (77-digit token ids) make a URL the server rejects with HTTP 414, and
        every started 100 pairs costs a heavy unit anyway; so calls carry at
        most 100 pairs and :data:`MARKS_MAX_CHARS` characters of them.
        """
        pairs = list(dict.fromkeys((str(m), str(t)) for m, t in tokens if m and t))
        found: dict[tuple[str, str], float] = {}
        async with self._client() as client:
            for batch in _url_batches(pairs, MARKS_BATCH, MARKS_MAX_CHARS):
                data = await request_json(
                    client,
                    'GET',
                    f'{self.market_data_url}/v1/marks',
                    params={
                        'provider': provider,
                        'pairs': ','.join(f'{m}:{t}' for m, t in batch),
                    },
                )
                for row in data.get('marks') or []:
                    key = (
                        str(row.get('contract_id') or ''),
                        str(row.get('token_id') or ''),
                    )
                    try:
                        found[key] = round(float(row['price']) / 100.0, 6)
                    except (KeyError, TypeError, ValueError):
                        continue
        return found

    async def trade_metrics(
        self,
        markets: Iterable[tuple[str, str]],
        *,
        window_seconds: int = 86400,
        concurrency: int = 4,
    ) -> dict[tuple[str, str], dict[str, float]]:
        """Trailing trade volume per ``(venue, market id)``, from ``/v1/trades/metrics``.

        One light request per market. Ids are the ones the rest of this module
        uses: the Kalshi ticker, or the numeric market id on the other venues
        (a Polymarket condition id comes back with no trades). Each result has
        ``volume_usd``, ``trade_count`` and ``coverage_pct``, the share of the
        window Kairos holds trade data for. Markets whose request failed are
        left out.

        Args:
            window_seconds: Trailing window, one to 24 hours.
        """
        if not 3600 <= window_seconds <= 86400:
            raise ValueError('window_seconds must be between 3600 and 86400')
        wanted = list(dict.fromkeys((str(v), str(m)) for v, m in markets if v and m))
        found: dict[tuple[str, str], dict[str, float]] = {}
        failed: list[str] = []
        async with self._client() as client:

            async def one(venue: str, market_id: str) -> None:
                try:
                    data = await request_json(
                        client,
                        'GET',
                        f'{self.market_data_url}/v1/trades/metrics',
                        params={
                            'provider': venue,
                            'contract_id': market_id,
                            'window_seconds': window_seconds,
                        },
                    )
                except APIError as exc:
                    failed.append(str(exc))
                    return
                metrics = data.get('metrics') or {}
                found[(venue, market_id)] = {
                    key: float(metrics.get(key) or 0.0)
                    for key in ('volume_usd', 'trade_count', 'coverage_pct')
                }

            await gather_limited(
                [one(venue, market_id) for venue, market_id in wanted],
                limit=concurrency,
            )
        if failed:
            logger.warning(
                'Kairos trade metrics failed for %d of %d markets: %s',
                len(failed),
                len(wanted),
                failed[0],
            )
        return found

    async def fee_quote(
        self,
        exchange: str,
        *,
        token_id: str = '',
        market_id: str = '',
        quantity: float,
        side: str = 'buy',
    ) -> dict[str, Any]:
        """Kairos's price for a market order of ``quantity`` shares, walked down the live book.

        Read-only: ``GET /orders/fee-quote`` places nothing. Needs an API key
        with the ``trade:read`` scope (a read-only key has it). Returns floats:
        ``avg_price``, ``filled``, ``exchange_fee``, ``platform_fee``,
        ``sufficient_liquidity``, or ``{'unavailable': True}`` when Kairos has
        no fresh order book.
        """
        if self.credentials is None:
            raise APIError('Kairos fee quotes need an API key (KAIROS_* variables)')
        params = {
            'exchange_id': exchange,
            'side': side,
            'order_type': 'market',
            'quantity': f'{quantity:g}',
        }
        if token_id:
            params['token_id'] = token_id
        if market_id:
            params['market_id'] = market_id
        async with self._client() as client:
            data = await request_json(
                client, 'GET', f'{self.execution_url}/orders/fee-quote', params=params
            )
        if data.get('pricing_unavailable'):
            return {'unavailable': True}

        def number(key: str) -> float:
            try:
                return float(data.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        return {
            'avg_price': number('avg_price_usdc'),
            'filled': number('filled_size'),
            'exchange_fee': number('exchange_fee_usdc'),
            'platform_fee': number('platform_fee_usdc'),
            'sufficient_liquidity': bool(data.get('sufficient_liquidity')),
        }
