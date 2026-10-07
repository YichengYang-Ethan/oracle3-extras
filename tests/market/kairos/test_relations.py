from __future__ import annotations

import json

import pytest
from oracle3.market.relations import MarketRelation, RelationStore

from oracle3_extras import venues
from oracle3_extras.market.align import OutcomeAlignment
from oracle3_extras.market.kairos import (
    DATA_API,
    MatchedPair,
    kairos_relations,
    save_relations,
    to_relation,
)
from tests.factories import gamma_market, kairos_page, kairos_pair, kalshi_market

NHL = kalshi_market(
    'KXNHLGAME-26OCT10DALPIT-DAL', 'Dallas', occurrence='2026-10-11T02:00:00Z'
)
NHL_POLY = gamma_market(
    '4527234',
    'Stars vs. Penguins',
    ['Stars', 'Penguins'],
    slug='nhl-dal-pit-2026-10-10',
)
TOTAL = kalshi_market(
    'KXNFLTOTAL-26OCT11BALATL-62',
    'Over 61.5 points',
    strike_type='greater',
    floor_strike=61.5,
    occurrence='2026-10-11T20:00:00Z',
)
TOTAL_POLY = gamma_market(
    '4061332',
    'Ravens vs. Falcons: O/U 60.5',
    ['Over', 'Under'],
    slug='nfl-bal-atl-2026-10-11-total-60pt5',
    sports_type='totals',
    line=60.5,
    start='2026-10-11 17:00:00+00',
)
CLOSED = kalshi_market('KXNHLGAME-26OCT01AAABBB-AAA', 'A', status='finalized')
CLOSED_POLY = gamma_market('7', 'A vs. B', ['A', 'B'], slug='nhl-aaa-bbb-2026-10-01')


def route_catalog(http) -> None:
    http.get(
        f'{DATA_API}/matched-markets',
        kairos_page(
            [
                kairos_pair(
                    NHL['ticker'], 'Dallas wins', '4527234', 'Stars vs. Penguins'
                ),
                kairos_pair(TOTAL['ticker'], 'over 61.5', '4061332', 'O/U 60.5', 0.97),
                kairos_pair(CLOSED['ticker'], 'A wins', '7', 'A vs. B'),
                kairos_pair('KXMISSING-26OCT10XY-X', 'X wins', '8', 'X vs. Y'),
                {
                    'a': {'provider': 'kalshi', 'market_id': 'KXOTHER'},
                    'b': {'provider': 'predictfun', 'market_id': '99'},
                    'similarity': 0.9,
                },
            ]
        ),
    )
    http.get(f'{venues.KALSHI_API}/markets', {'markets': [NHL, TOTAL, CLOSED]})
    http.get(f'{venues.GAMMA_API}/markets', [NHL_POLY, TOTAL_POLY, CLOSED_POLY])


async def test_kairos_relations_aligns_and_explains_the_rest(http) -> None:
    route_catalog(http)
    result = await kairos_relations()
    assert [r.relation_id for r in result.relations] == [
        'kairos:KXNHLGAME-26OCT10DALPIT-DAL:4527234'
    ]
    reasons = sorted(reason for _, reason in result.rejected)
    assert reasons == [
        'Kalshi market is finalized',
        'Kalshi market not found',
        'different lines: Kalshi 61.5, Polymarket 60.5',
    ]
    summary = result.summary()
    assert summary['pairs'] == 5 and summary['kalshi_polymarket'] == 4
    assert summary['pairs_by_venues'] == {
        'kalshi + polymarket': 4,
        'kalshi + predictfun': 1,
    }
    assert summary['rejected']['different lines'] == 1


async def test_relation_fields_follow_oracle3(http) -> None:
    route_catalog(http)
    relation = (await kairos_relations()).relations[0]
    assert relation.spread_type == 'same_event'
    assert relation.hypothesis == 'P(A) = P(B)'
    assert relation.status == 'discovered'
    assert relation.confidence == 1.0
    assert relation.market_a['venue'] == 'kalshi'
    assert relation.market_a['yes_outcome'] == 'Dallas'
    assert relation.market_b['venue'] == 'polymarket'
    assert (relation.market_b['token_id'], relation.market_b['no_token_id']) == (
        'tok-a',
        'tok-b',
    )
    assert relation.valid_until == '2026-10-10T23:00:00Z'
    assert 'team code' in relation.reasoning


def test_to_relation_refuses_unaligned_pairs() -> None:
    pair = MatchedPair.from_api(kairos_pair(NHL['ticker'], 'x', '1', 'y'))
    with pytest.raises(ValueError):
        to_relation(pair, NHL, NHL_POLY, OutcomeAlignment.reject('other', 'nope'))


def relation(
    rid: str, status: str = 'discovered', confidence: float = 0.9
) -> MarketRelation:
    return MarketRelation(
        relation_id=rid,
        market_a={'venue': 'kalshi', 'market_id': 'A'},
        market_b={'venue': 'polymarket', 'market_id': 'B'},
        spread_type='same_event',
        confidence=confidence,
        status=status,
    )


def test_save_relations_merges_without_losing_lifecycle(tmp_path) -> None:
    store = tmp_path / 'relations.json'
    deployed = relation('kairos:K1:P1', status='deployed')
    deployed.validation = {'analysis_type': 'structural'}
    stale = relation('kairos:K2:P2')
    manual = relation('manual:pair')
    store.write_text(
        json.dumps([deployed.to_dict(), stale.to_dict(), manual.to_dict()])
    )

    counts = save_relations(
        [relation('kairos:K1:P1', confidence=0.99), relation('kairos:K3:P3')], store
    )
    assert counts == {'added': 1, 'updated': 1, 'removed': 1, 'kept': 1}
    rows = {r['relation_id']: r for r in json.loads(store.read_text())}
    assert set(rows) == {'kairos:K1:P1', 'manual:pair', 'kairos:K3:P3'}
    assert rows['kairos:K1:P1']['status'] == 'deployed'
    assert rows['kairos:K1:P1']['validation'] == {'analysis_type': 'structural'}
    assert rows['kairos:K1:P1']['confidence'] == 0.99


def test_saved_relations_load_in_oracle3(tmp_path) -> None:
    store = tmp_path / 'relations.json'
    save_relations([relation('kairos:K1:P1')], store, prune=False)
    loaded = RelationStore(store).list(spread_type='same_event')
    assert [r.relation_id for r in loaded] == ['kairos:K1:P1']
    assert RelationStore(store).find_by_market('B')
