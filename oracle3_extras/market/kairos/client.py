"""Client for the public Kairos Data API (``https://data.kairos.trade``).

Kairos matches the same market across Kalshi, Polymarket, Predict.fun and
Hyperliquid by embedding similarity and publishes the verified pairs as a
catalog. The endpoints used here need no account; an API key raises the rate
limits (``KAIROS_CLIENT_ID``, ``KAIROS_API_KEY`` and ``KAIROS_API_SECRET``).

Requests identify themselves with the oracle3-extras User-Agent and nothing
else about the user.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from oracle3_extras._http import APIError, chunks, new_client, request_json

__all__ = [
    'DATA_API',
    'MIN_SIMILARITY',
    'KairosClient',
    'KairosCredentials',
    'KairosMarket',
    'MatchedMarkets',
    'MatchedPair',
]

DATA_API = 'https://data.kairos.trade'

#: Kairos never publishes a pair below this similarity, whatever the caller asks.
MIN_SIMILARITY = 0.82

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KairosMarket:
    """One side of a matched pair, in the venue's own identifiers.

    ``market_id`` is the Kalshi ticker, the numeric Polymarket (Gamma) market
    id, the Predict.fun market id or the Hyperliquid outcome id. For Polymarket,
    ``ticker`` holds the condition id.
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
        page_size: Pairs per catalog page (Kairos allows up to 1,000).
        max_restarts: How many times to restart a catalog walk that Kairos
            reports as changed mid-walk (HTTP 409).
    """

    def __init__(
        self,
        *,
        credentials: KairosCredentials | None = None,
        base_url: str = DATA_API,
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
