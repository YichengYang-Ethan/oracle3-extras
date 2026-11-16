from __future__ import annotations

import json

import pytest

from oracle3_extras._http import APIError
from oracle3_extras.market.kairos import (
    DATA_API,
    KairosClient,
    KairosCredentials,
    MatchedPair,
)
from tests.factories import (
    kairos_details,
    kairos_link,
    kairos_links_page,
    kairos_pair,
    link_from_pair,
)

LINKS = f'{DATA_API}/market-links/featured'
DETAILS = f'{DATA_API}/markets/details'
P1 = kairos_pair('KXA-26OCT10AB-A', 'A wins', '1', 'A vs. B')
P2 = kairos_pair('KXC-26OCT10CD-C', 'C wins', '2', 'C vs. D')
L1, L2 = link_from_pair(P1), link_from_pair(P2)


def walks(http) -> list:
    return [r for r in http.requests if str(r.url).startswith(LINKS)]


async def test_walks_every_cursor_page_and_dedupes(http) -> None:
    http.get(
        LINKS, kairos_links_page([L1], next_cursor='c1'), kairos_links_page([L1, L2])
    )
    http.add('POST', DETAILS, kairos_details(P1, P2))
    catalog = await KairosClient().matched_markets(provider='kalshi')
    assert [p.side('kalshi').market_id for p in catalog.pairs] == [
        'KXA-26OCT10AB-A',
        'KXC-26OCT10CD-C',
    ]
    first, second = walks(http)
    assert first.url.params['limit'] == '100' and 'cursor' not in first.url.params
    assert second.url.params['cursor'] == 'c1'


async def test_a_link_becomes_one_pair_per_two_venues(http) -> None:
    link = kairos_link(
        ('kalshi', 'KXA-26OCT10AB-A'),
        ('polymarket', '1'),
        ('predictfun', '77'),
        link_id='L9',
        title='A vs. B',
        end_time='2026-10-11T03:00:00Z',
    )
    http.get(LINKS, kairos_links_page([link]))
    http.add(
        'POST', DETAILS, kairos_details(P1)
    )  # Kairos knows the Kalshi and Polymarket markets
    catalog = await KairosClient().matched_markets()
    assert sorted(' + '.join(sorted(p.providers)) for p in catalog.pairs) == [
        'kalshi + polymarket',
        'kalshi + predictfun',
        'polymarket + predictfun',
    ]
    pair = catalog.between('kalshi', 'polymarket')[0]
    assert pair.link_id == 'L9' and pair.similarity == 1.0
    poly, kalshi = pair.side('polymarket'), pair.side('kalshi')
    assert poly.market_id == '1' and poly.provider_id == 2  # the Gamma id, not 0xcond1
    assert (kalshi.title, kalshi.category) == ('A wins', 'Sports')
    assert kalshi.expires_at == '2026-10-11T03:00:00Z'
    predictfun = catalog.between('kalshi', 'predictfun')[0].side('predictfun')
    assert (predictfun.title, predictfun.category) == ('A vs. B', None)
    [request] = [r for r in http.requests if str(r.url) == DETAILS]
    asked = {
        (m['provider_id'], m['market_id'])
        for m in json.loads(request.content)['markets']
    }
    assert asked == {(1, 'KXA-26OCT10AB-A'), (2, '1'), (8, '77')}


async def test_provider_filter_keeps_pairs_with_a_side_there(http) -> None:
    link = kairos_link(('kalshi', 'KXA'), ('polymarket', '1'), ('predictfun', '77'))
    http.get(LINKS, kairos_links_page([link]))
    http.add('POST', DETAILS, {'markets': []})
    catalog = await KairosClient().matched_markets(provider='polymarket')
    assert len(catalog.pairs) == 2
    assert all('polymarket' in p.providers for p in catalog.pairs)


async def test_restarts_when_the_cursor_goes_stale(http) -> None:
    http.get(
        LINKS,
        kairos_links_page([L1], next_cursor='c1'),
        (400, {'detail': 'invalid cursor'}),
        kairos_links_page([L1, L2]),
    )
    http.add('POST', DETAILS, kairos_details(P1, P2))
    catalog = await KairosClient().matched_markets()
    assert len(catalog.pairs) == 2
    assert [r.url.params.get('cursor') for r in walks(http)] == [None, 'c1', None]


async def test_gives_up_after_max_restarts(http) -> None:
    http.get(
        LINKS, kairos_links_page([L1], next_cursor='c1'), (409, {'detail': 'changed'})
    )
    with pytest.raises(APIError):
        await KairosClient(max_restarts=0).matched_markets()


async def test_a_bad_first_page_is_an_error(http) -> None:
    http.get(LINKS, (400, {'detail': 'bad limit'}))
    with pytest.raises(APIError):
        await KairosClient().matched_markets()
    assert len(http.requests) == 1


async def test_stops_on_a_repeated_cursor(http) -> None:
    http.get(LINKS, kairos_links_page([L1], next_cursor='same'))
    http.add('POST', DETAILS, kairos_details(P1))
    catalog = await KairosClient().matched_markets()
    assert len(catalog.pairs) == 1 and len(walks(http)) == 2


async def test_api_key_headers(http) -> None:
    http.get(LINKS, kairos_links_page([]))
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


async def test_market_details_batches_and_finds_polymarket_by_either_id(http) -> None:
    def reply(request):
        asked = json.loads(request.content)['markets']
        rows = [
            {'market_id': m['market_id'], 'provider_id': 1, 'category': 'Sports'}
            for m in asked
            if m['provider_id'] == 1
        ]
        if any(m['provider_id'] == 2 for m in asked):
            rows.append(
                {'market_id': '7054891', 'provider_id': 2, 'condition_id': '0xabc'}
            )
        return {'markets': rows}

    http.add('POST', DETAILS, reply)
    refs = [('kalshi', f'KX-{i}') for i in range(500)] + [('polymarket', '0xabc')]
    found = await KairosClient().market_details(refs + [('opinion', '1')])
    assert len(http.requests) == 2  # 500 per call; unknown venues are not sent
    assert found[('kalshi', 'KX-499')]['category'] == 'Sports'
    assert found[('polymarket', '0xabc')] is found[('polymarket', '7054891')]
