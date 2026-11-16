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


CLUSTERS = f'{DATA_API}/market-clusters'
ACTIVE = f'{DATA_API}/markets/active'


def member(provider: str, market_id: str, title: str) -> dict:
    ids = {'kalshi': 1, 'polymarket': 2, 'predictfun': 8}
    return {
        'provider_id': ids[provider],
        'provider': provider,
        'market_id': market_id,
        'title': title,
        'ticker': None,
        'expires_at': '2027-01-01T00:00:00Z',
    }


def cluster_reply(request):
    """``pf77`` sits with Polymarket 1 and 5; only Polymarket 5 survives the semantic floor."""
    wide = [
        member('predictfun', '77', 'Will X launch?'),
        member('polymarket', '1', 'A vs. B'),
        member('polymarket', '5', 'Will X launch?'),
    ]
    members = wide if request.url.params['floor'] == 'exact' else [wide[0], wide[2]]
    return {
        'clusters': {
            '8:77': {
                'cluster_id': 'c1',
                'floor': request.url.params['floor'],
                'truncated': False,
                'size': len(members),
                'members': members,
            }
        },
        'count': 1,
        'floor': request.url.params['floor'],
    }


async def test_cluster_seeds_add_pairs_and_links_win(http) -> None:
    link = kairos_link(('kalshi', 'KXA'), ('polymarket', '1'), ('predictfun', '77'))
    http.get(LINKS, kairos_links_page([link]))
    http.get(CLUSTERS, cluster_reply)
    http.add(
        'POST',
        DETAILS,
        {
            'markets': [
                {'market_id': '5', 'provider_id': 2, 'name': 'X', 'category': 'Crypto'},
                {
                    'market_id': '77',
                    'provider_id': 8,
                    'name': 'X',
                    'category': 'Crypto',
                },
            ]
        },
    )
    catalog = await KairosClient().matched_markets(cluster_seeds=[('predictfun', '77')])
    by_key = {p.key: p for p in catalog.pairs}
    linked = by_key[(('polymarket', '1'), ('predictfun', '77'))]
    assert linked.link_id == 'link-1' and linked.cluster_id == ''  # the link wins
    clustered = by_key[(('polymarket', '5'), ('predictfun', '77'))]
    assert (clustered.cluster_id, clustered.tier) == ('c1', 'semantic')
    assert clustered.side('polymarket').category == 'Crypto'
    assert (('polymarket', '1'), ('polymarket', '5')) not in by_key  # one venue
    assert len(catalog.pairs) == 4  # three from the link, one from the cluster
    floors = sorted(r.url.params['floor'] for r in http.calls(CLUSTERS))
    assert floors == ['exact', 'semantic']


async def test_exact_only_pairs_are_marked_exact(http) -> None:
    http.get(LINKS, kairos_links_page([]))
    http.get(CLUSTERS, cluster_reply)
    http.add('POST', DETAILS, {'markets': []})
    catalog = await KairosClient().matched_markets(cluster_seeds=[('predictfun', '77')])
    tiers = {p.side('polymarket').market_id: p.tier for p in catalog.pairs}
    assert tiers == {'1': 'exact', '5': 'semantic'}


async def test_no_seeds_no_cluster_requests(http) -> None:
    http.get(LINKS, kairos_links_page([]))
    await KairosClient().matched_markets(cluster_seeds=[])
    assert http.calls(CLUSTERS) == []


async def test_active_markets_pages_and_drops_the_raw_payload(http) -> None:
    def page(market_id: str, cursor: str | None) -> dict:
        market = {
            'market_id': market_id,
            'title': 't',
            'category': 'Crypto',
            'raw': {'big': 'x' * 100},
        }
        return {
            'markets': [market],
            'has_more': cursor is not None,
            'next_cursor': cursor,
        }

    http.get(ACTIVE, page('1', 'c1'), page('2', None))
    markets = await KairosClient().active_markets('predictfun')
    assert [m['market_id'] for m in markets] == ['1', '2']
    assert 'raw' not in markets[0] and markets[0]['category'] == 'Crypto'
    first, second = http.calls(ACTIVE)
    assert first.url.params['provider'] == 'predictfun'
    assert first.url.params['limit'] == '250' and second.url.params['cursor'] == 'c1'
