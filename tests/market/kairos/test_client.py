from __future__ import annotations

import pytest

from oracle3_extras._http import APIError
from oracle3_extras.market.kairos import (
    DATA_API,
    KairosClient,
    KairosCredentials,
    MatchedPair,
)
from tests.factories import kairos_page, kairos_pair

MATCHED = f'{DATA_API}/matched-markets'
P1 = kairos_pair('KXA-26OCT10AB-A', 'A wins', '1', 'A vs. B')
P2 = kairos_pair('KXC-26OCT10CD-C', 'C wins', '2', 'C vs. D')


async def test_walks_every_cursor_page_and_dedupes(http) -> None:
    flipped = {
        **P1,
        'a': P1['b'],
        'b': P1['a'],
    }  # the same pair listed the other way round
    http.get(MATCHED, kairos_page([P1], next_cursor='c1'), kairos_page([flipped, P2]))
    catalog = await KairosClient().matched_markets(provider='kalshi')
    assert [p.side('kalshi').market_id for p in catalog.pairs] == [
        'KXA-26OCT10AB-A',
        'KXC-26OCT10CD-C',
    ]
    assert catalog.catalog_version == 'v1'
    first, second = http.requests
    assert first.url.params['cursor'] == '' and first.url.params['provider'] == 'kalshi'
    assert second.url.params['cursor'] == 'c1'


async def test_restarts_when_the_catalog_changes_mid_walk(http) -> None:
    http.get(
        MATCHED,
        kairos_page([P1], next_cursor='c1'),
        (409, {'detail': 'matched-markets catalogue changed between cursor pages'}),
        kairos_page([P1, P2], version='v2'),
    )
    catalog = await KairosClient().matched_markets()
    assert len(catalog.pairs) == 2 and catalog.catalog_version == 'v2'
    assert [r.url.params['cursor'] for r in http.requests] == ['', 'c1', '']


async def test_gives_up_after_max_restarts(http) -> None:
    http.get(MATCHED, kairos_page([P1], next_cursor='c1'), (409, {'detail': 'changed'}))
    with pytest.raises(APIError):
        await KairosClient(max_restarts=0).matched_markets()


async def test_stops_on_a_repeated_cursor(http) -> None:
    http.get(MATCHED, kairos_page([P1], next_cursor='same'))
    catalog = await KairosClient().matched_markets()
    assert len(catalog.pairs) == 1 and len(http.requests) == 2


async def test_api_key_headers(http) -> None:
    http.get(MATCHED, kairos_page([]))
    creds = KairosCredentials('kairos_ck_1', 'k' * 64, 's' * 64)
    await KairosClient(credentials=creds).matched_markets()
    headers = http.requests[0].headers
    assert (
        headers['X-Client-Id'] == 'kairos_ck_1' and headers['X-Api-Secret'] == 's' * 64
    )
    assert 's' * 64 not in repr(creds)


def test_credentials_from_env_need_all_three() -> None:
    env = {'KAIROS_CLIENT_ID': 'id', 'KAIROS_API_KEY': 'key'}
    assert KairosCredentials.from_env(env) is None
    assert (
        KairosCredentials.from_env({**env, 'KAIROS_API_SECRET': 'secret'}) is not None
    )


async def test_market_clusters_batches_references(http) -> None:
    def reply(request):
        refs = request.url.params['markets'].split(',')
        assert request.url.params['floor'] == 'exact'
        return {
            'clusters': {r: {'members': []} for r in refs},
            'count': len(refs),
            'floor': 'exact',
        }

    http.get(f'{DATA_API}/market-clusters', reply)
    found = await KairosClient().market_clusters((1, f'KX-{i}') for i in range(250))
    assert len(found) == 250 and len(http.requests) == 2
    with pytest.raises(ValueError):
        await KairosClient().market_clusters([], floor='fuzzy')


def test_pair_helpers() -> None:
    pair = MatchedPair.from_api(P1)
    assert pair.providers == {'kalshi', 'polymarket'}
    assert pair.side('polymarket').market_id == '1'
    assert pair.side('predictfun') is None


def test_page_size_is_validated() -> None:
    with pytest.raises(ValueError):
        KairosClient(page_size=5000)
