"""HTTP plumbing shared by the read-only integrations.

Every request carries the same User-Agent, so each service can see the traffic
oracle3-extras sends, and follows the same retry rule: 429 and 5xx responses
are retried after the server's ``Retry-After`` (or an exponential backoff) up
to a cap. Tests route every request through an ``httpx.MockTransport`` with
:func:`set_transport`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from typing import Any, TypeVar

import httpx

from oracle3_extras._version import __version__

__all__ = [
    'USER_AGENT',
    'APIError',
    'chunks',
    'gather_limited',
    'new_client',
    'request_json',
    'set_transport',
]

USER_AGENT = (
    f'oracle3-extras/{__version__} '
    '(+https://github.com/YichengYang-Ethan/oracle3-extras)'
)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

T = TypeVar('T')

logger = logging.getLogger(__name__)

_transport: httpx.AsyncBaseTransport | None = None
_sleep = asyncio.sleep


class APIError(RuntimeError):
    """A request failed, or the service answered with an error status."""

    def __init__(
        self, message: str, *, status: int | None = None, url: str = ''
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Send every request through ``transport`` (tests pass a mock transport)."""
    global _transport
    _transport = transport


def new_client(
    *, timeout: float = 20.0, headers: Mapping[str, str] | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
            **(headers or {}),
        },
        transport=_transport,
        follow_redirects=True,
    )


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(float(response.headers.get('Retry-After', '')), 0.0)
    except ValueError:
        return None


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Any = None,
    json: Any = None,
    retries: int = 3,
    max_wait: float = 60.0,
) -> Any:
    """Send one request and decode the JSON body, retrying 429 and 5xx answers."""
    attempt = 0
    while True:
        try:
            response = await client.request(method, url, params=params, json=json)
        except httpx.HTTPError as exc:
            if attempt >= retries:
                raise APIError(f'{method} {url} failed: {exc}', url=url) from exc
            wait = min(2.0**attempt, max_wait)
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise APIError(
                        f'{url} returned a body that is not JSON',
                        status=response.status_code,
                        url=url,
                    ) from exc
            if response.status_code not in RETRY_STATUSES or attempt >= retries:
                raise APIError(
                    f'{url} returned HTTP {response.status_code}: '
                    f'{response.text[:200]}',
                    status=response.status_code,
                    url=url,
                )
            wait = min(_retry_after(response) or 2.0**attempt, max_wait)
            logger.debug(
                'HTTP %s from %s; retrying in %.0fs', response.status_code, url, wait
            )
        attempt += 1
        await _sleep(wait)


def chunks(items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def gather_limited(jobs: Iterable[Awaitable[T]], limit: int = 4) -> list[T]:
    """Await ``jobs`` with at most ``limit`` in flight, keeping their order."""
    semaphore = asyncio.Semaphore(limit)

    async def run(job: Awaitable[T]) -> T:
        async with semaphore:
            return await job

    return list(await asyncio.gather(*(run(job) for job in jobs)))
