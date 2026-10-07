"""Shared fixtures: an isolated home directory, a fake ``mm`` CLI and a mock HTTP router."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from oracle3_extras import _http
from tests.trader.metamask.support import FakeMM


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        '--live',
        action='store_true',
        help='also run tests that call the real Kairos, Kalshi and Polymarket APIs',
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption('--live'):
        return
    skip = pytest.mark.skip(reason='calls real APIs; run with --live')
    for item in items:
        if 'live' in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep a real ~/.oracle3 (kill switch, relation store) out of every test."""
    monkeypatch.delenv('PRED_MARKET_CLI_KILL_SWITCH', raising=False)
    monkeypatch.delenv('PRED_MARKET_CLI_KILL_SWITCH_FILE', raising=False)
    for name in ('KAIROS_CLIENT_ID', 'KAIROS_API_KEY', 'KAIROS_API_SECRET'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    os.makedirs(tmp_path / 'home', exist_ok=True)


@pytest.fixture
def fake_mm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMM:
    return FakeMM(tmp_path, monkeypatch)


Reply = Any


class Router:
    """Answers requests with canned replies and records every request.

    A reply is a JSON body, a ``(status, body)`` or ``(status, body, headers)``
    tuple, or a function of the request returning one of those. Replies queued
    for one URL are used in order and the last one repeats.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Reply]] = {}
        self.requests: list[httpx.Request] = []

    def add(self, method: str, url: str, *replies: Reply) -> Router:
        self.routes.setdefault((method.upper(), url), []).extend(replies)
        return self

    def get(self, url: str, *replies: Reply) -> Router:
        return self.add('GET', url, *replies)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url).split('?')[0])
        queue = self.routes.get(key)
        if not queue:
            raise AssertionError(f'unexpected request: {request.method} {request.url}')
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(reply):
            reply = reply(request)
        status, body, headers = 200, reply, {}
        if isinstance(reply, tuple):
            status, body = reply[0], reply[1]
            headers = reply[2] if len(reply) > 2 else {}
        return httpx.Response(status, json=body, headers=headers)

    def calls(self, url: str = '') -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).startswith(url)]


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> Iterator[Router]:
    """Route every HTTP request to a :class:`Router`; retries do not sleep."""
    router = Router()
    waits: list[float] = []

    async def no_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(_http, '_sleep', no_sleep)
    _http.set_transport(httpx.MockTransport(router))
    router.waits = waits  # type: ignore[attr-defined]
    yield router
    _http.set_transport(None)
