from __future__ import annotations

import asyncio
import gc
import warnings

import pytest

from oracle3_extras._http import (
    USER_AGENT,
    APIError,
    chunks,
    gather_limited,
    new_client,
    request_json,
)

URL = 'https://api.example.test/things'


async def test_returns_json_and_sends_user_agent(http) -> None:
    http.get(URL, {'ok': 1})
    async with new_client() as client:
        assert await request_json(client, 'GET', URL) == {'ok': 1}
    assert http.requests[0].headers['User-Agent'] == USER_AGENT
    assert USER_AGENT.startswith('oracle3-extras/')


async def test_retries_429_after_retry_after(http) -> None:
    http.get(URL, (429, {'error': 'slow down'}, {'Retry-After': '7'}), {'ok': 2})
    async with new_client() as client:
        assert await request_json(client, 'GET', URL) == {'ok': 2}
    assert http.waits == [7.0]


async def test_retries_5xx_with_backoff_then_gives_up(http) -> None:
    http.get(URL, (503, {'detail': 'down'}))
    async with new_client() as client:
        with pytest.raises(APIError) as caught:
            await request_json(client, 'GET', URL, retries=2)
    assert caught.value.status == 503
    assert http.waits == [1.0, 2.0]
    assert len(http.requests) == 3


async def test_client_errors_are_not_retried(http) -> None:
    http.get(URL, (409, {'detail': 'catalogue changed'}))
    async with new_client() as client:
        with pytest.raises(APIError) as caught:
            await request_json(client, 'GET', URL)
    assert caught.value.status == 409
    assert len(http.requests) == 1


async def test_retry_wait_is_capped(http) -> None:
    http.get(URL, (429, {}, {'Retry-After': '3600'}), {'ok': 3})
    async with new_client() as client:
        await request_json(client, 'GET', URL, max_wait=30)
    assert http.waits == [30]


async def test_gather_limited_keeps_order() -> None:
    async def double(x: int) -> int:
        return 2 * x

    assert await gather_limited((double(i) for i in range(5)), limit=2) == [
        0,
        2,
        4,
        6,
        8,
    ]


def test_chunks() -> None:
    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


async def test_gather_limited_cancels_the_rest_after_a_failure() -> None:
    started: list[int] = []

    async def job(n: int) -> int:
        started.append(n)
        await asyncio.sleep(0 if n == 0 else 10)
        if n == 0:
            raise RuntimeError('boom')
        return n

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with pytest.raises(RuntimeError, match='boom'):
            await gather_limited([job(n) for n in range(6)], limit=2)
        gc.collect()
    assert 0 in started and len(started) < 6  # queued jobs were cancelled
    assert not [w for w in caught if 'never awaited' in str(w.message)]
