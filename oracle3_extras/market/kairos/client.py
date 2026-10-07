"""Client for Kairos's public Data API and Market Data API.

Kairos matches the same market across Kalshi, Polymarket, Predict.fun and
Hyperliquid by embedding similarity and publishes the verified pairs as a
catalog (``https://data.kairos.trade``). Its Market Data API
(``https://md.kairos.trade``) serves normalized candles and settlement results
for every venue it covers. The endpoints used here need no account; an API key
raises the rate limits (``KAIROS_CLIENT_ID``, ``KAIROS_API_KEY`` and
``KAIROS_API_SECRET``).

Requests identify themselves with the oracle3-extras User-Agent and nothing
else about the user.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from oracle3_extras import _http
from oracle3_extras._http import APIError, chunks, new_client, request_json

__all__ = [
    'DATA_API',
    'MARKET_DATA_API',
    'MIN_SIMILARITY',
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

#: Candle widths the Market Data API serves, in seconds.
TIMEFRAMES = (1, 60, 300, 900, 3600, 14400, 86400)

#: Kairos never publishes a pair below this similarity, whatever the caller asks.
MIN_SIMILARITY = 0.82

logger = logging.getLogger(__name__)


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
    """Two markets that Kairos matched as the same underlying question."""

    a: KairosMarket
    b: KairosMarket
    similarity: float
    updated_at: str = ''

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
        page_size: Pairs per catalog page (Kairos allows up to 1,000).
        max_restarts: How many times to restart a catalog walk that Kairos
            reports as changed mid-walk (HTTP 409).
    """

    def __init__(
        self,
        *,
        credentials: KairosCredentials | None = None,
        base_url: str = DATA_API,
        market_data_url: str = MARKET_DATA_API,
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
        self.page_size = page_size
        self.max_restarts = max_restarts
        self.timeout = timeout

    def _client(self):  # noqa: ANN202
        headers = self.credentials.headers() if self.credentials else None
        return new_client(timeout=self.timeout, headers=headers)

    async def matched_markets(
        self, *, provider: str | None = None, min_similarity: float | None = None
    ) -> MatchedMarkets:
        """The whole verified pair catalog, walked with Kairos's keyset cursor.

        Args:
            provider: Only pairs with a side on this venue (``kalshi``,
                ``polymarket``, ``predictfun`` or ``hyperliquid``).
            min_similarity: Similarity floor; Kairos raises anything below
                :data:`MIN_SIMILARITY` to that value.

        Every page of one walk comes from the same catalog version. If the
        catalog changes mid-walk Kairos answers 409, and the walk restarts.
        """
        for attempt in range(self.max_restarts + 1):
            try:
                return await self._walk(provider, min_similarity)
            except APIError as exc:
                if exc.status != 409 or attempt == self.max_restarts:
                    raise
                logger.info('Kairos catalog changed during the walk; restarting')
        raise AssertionError('unreachable')  # pragma: no cover

    async def _walk(
        self, provider: str | None, min_similarity: float | None
    ) -> MatchedMarkets:
        params: dict[str, Any] = {'limit': self.page_size, 'cursor': ''}
        if provider:
            params['provider'] = provider
        if min_similarity is not None:
            params['min_similarity'] = min_similarity
        pairs: list[MatchedPair] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        cursors: set[str] = set()
        version = ''
        async with self._client() as client:
            while True:
                data = await request_json(
                    client, 'GET', f'{self.base_url}/matched-markets', params=params
                )
                version = str(data.get('catalog_version') or version)
                for raw in data.get('pairs') or []:
                    pair = MatchedPair.from_api(raw)
                    if pair.key not in seen:
                        seen.add(pair.key)
                        pairs.append(pair)
                cursor = data.get('next_cursor')
                if not data.get('has_more') or not cursor or cursor in cursors:
                    break
                cursors.add(cursor)
                params['cursor'] = cursor
        logger.info(
            'Kairos: %d matched pairs (%d requests)', len(pairs), len(cursors) + 1
        )
        return MatchedMarkets(pairs=pairs, catalog_version=version)

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
        batch_size: int = 25,
        pause: float = 1.0,
    ) -> list[CandleSeries]:
        """Candle series in request order, from ``/v1/candles/batch``.

        Kairos accepts up to 200 series per call, but large one-minute batches
        are expensive for it to build, so this sends ``batch_size`` at a time
        and waits ``pause`` seconds between calls. If Kairos still fails after
        the usual retries, the remaining series are marked with the error
        instead of sending more requests.

        Kairos keeps 1-minute candles for 30 days, 1-hour for 365 and daily for
        730; longer windows are clamped. Candles exist only for buckets with
        trades. A series Kairos rejects comes back with ``error`` set.
        """
        if not 1 <= batch_size <= 200:
            raise ValueError('batch_size must be between 1 and 200')
        results = [CandleSeries(request=r) for r in requests]
        async with self._client() as client:
            for offset in range(0, len(results), batch_size):
                batch = results[offset : offset + batch_size]
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
