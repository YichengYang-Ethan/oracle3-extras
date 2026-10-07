from __future__ import annotations

import pytest
from oracle3.arbitrage import Quote, check_constraint
from oracle3.fees import KalshiSchedule, PolymarketSchedule
from oracle3.market.relations import MarketRelation

from oracle3_extras import venues
from oracle3_extras.arbitrage import scan_relations, walk_books
from oracle3_extras.venues import Book
from tests.factories import gamma_market, kalshi_market

FREE = PolymarketSchedule(rate=0, source='test')


def basket():
    a = Quote('A', venue='kalshi', yes_ask=0.40, schedule=FREE)
    b = Quote('B', venue='polymarket', yes_bid=0.50, schedule=FREE)
    best = check_constraint('same_event', [a, b]).best
    assert best is not None and best.description == 'YES on A + NO on B'
    return best


BOOKS = {
    ('kalshi', 'A'): Book('kalshi', 'A', yes_asks=[(0.40, 10), (0.45, 10)]),
    ('polymarket', 'B'): Book('polymarket', 'B', no_asks=[(0.50, 5), (0.52, 20)]),
}
SCHEDULES = {('kalshi', 'A'): FREE, ('polymarket', 'B'): FREE}


def test_walk_takes_levels_until_a_book_runs_out() -> None:
    result = walk_books(basket(), BOOKS, SCHEDULES)
    assert result is not None
    assert (result.contracts, result.cost, result.stopped_by) == (20, 18.8, 'book')
    assert result.net_edge == pytest.approx(1.2)
    a_leg, b_leg = result.legs
    assert (
        a_leg['average_price'] == pytest.approx(0.425) and a_leg['worst_price'] == 0.45
    )
    assert b_leg['contracts'] == 20


def test_walk_stops_at_the_cap_and_at_the_edge_floor() -> None:
    assert walk_books(basket(), BOOKS, SCHEDULES, max_contracts=7).stopped_by == 'cap'
    floor = walk_books(basket(), BOOKS, SCHEDULES, min_edge=0.05)
    assert (floor.contracts, floor.stopped_by) == (10, 'edge')


def test_walk_charges_fees_level_by_level() -> None:
    schedules = {
        ('kalshi', 'A'): KalshiSchedule(),
        ('polymarket', 'B'): PolymarketSchedule(),
    }
    result = walk_books(basket(), BOOKS, schedules)
    assert result.fees > 0
    assert result.net_edge == pytest.approx(
        result.contracts - result.cost - result.fees
    )


def test_walk_returns_none_when_the_first_level_loses() -> None:
    books = dict(BOOKS)
    books[('polymarket', 'B')] = Book('polymarket', 'B', no_asks=[(0.61, 5)])
    assert walk_books(basket(), books, SCHEDULES) is None


# ── scan_relations ───────────────────────────────────────────────────────


def rel(rid, a, b, kind='same_event'):
    return MarketRelation(relation_id=rid, market_a=a, market_b=b, spread_type=kind)


K1 = kalshi_market(
    'KXNHLGAME-26OCT10DALPIT-DAL', 'Dallas', yes_bid='0.3800', yes_ask='0.4000'
)
K2 = kalshi_market(
    'KXNHLGAME-26OCT10BOSNYR-BOS', 'Boston', yes_bid='0.5000', yes_ask='0.5200'
)
P1 = gamma_market(
    '1',
    'Stars vs. Penguins',
    ['Stars', 'Penguins'],
    slug='s1',
    best_bid=0.55,
    best_ask=0.57,
    tokens=('p1a', 'p1b'),
)
P2 = gamma_market(
    '2',
    'Bruins vs. Rangers',
    ['Rangers', 'Bruins'],
    slug='s2',
    best_bid=0.47,
    best_ask=0.49,
    tokens=('p2a', 'p2b'),
)
RELATIONS = [
    rel(
        'r1',
        {'venue': 'kalshi', 'market_id': K1['ticker']},
        {'venue': 'polymarket', 'market_id': '1'},
    ),
    rel(
        'r2',
        {'venue': 'kalshi', 'market_id': K2['ticker']},
        {'venue': 'polymarket', 'market_id': '2'},
        'complement',
    ),
    rel(
        'r3',
        {'venue': 'kalshi', 'market_id': K1['ticker']},
        {'venue': 'polymarket', 'market_id': '1'},
        'cointegration',
    ),
    rel('r4', {'symbol': 'X'}, {'symbol': 'Y'}),
]


def route_venues(http, *, kalshi_no_bid='0.6000', poly_bid='0.55') -> None:
    http.get(f'{venues.KALSHI_API}/markets', {'markets': [K1, K2]})
    http.get(
        f'{venues.KALSHI_API}/series/KXNHLGAME',
        {'series': {'fee_type': 'quadratic_with_maker_fees', 'fee_multiplier': 1}},
    )
    http.get(f'{venues.GAMMA_API}/markets', [P1, P2])
    http.get(
        f'{venues.KALSHI_API}/markets/orderbooks',
        {
            'orderbooks': [
                {
                    'ticker': K1['ticker'],
                    'orderbook_fp': {
                        'yes_dollars': [['0.3800', '50']],
                        'no_dollars': [[kalshi_no_bid, '30']],
                    },
                },
            ]
        },
    )
    http.add(
        'POST',
        f'{venues.CLOB_API}/books',
        [
            {'asset_id': 'p1a', 'asks': [{'price': '0.57', 'size': '100'}]},
            {
                'asset_id': 'p1b',
                'asks': [{'price': f'{1 - float(poly_bid):.2f}', 'size': '40'}],
            },
        ],
    )


async def test_scan_finds_sizes_and_skips(http) -> None:
    route_venues(http)
    report = await scan_relations(RELATIONS, contracts=1)
    summary = report.summary()
    assert summary['relations'] == 4 and summary['quoted'] == 2
    assert summary['skipped'] == {
        'relation type cointegration is not checked': 1,
        'markets need venue (kalshi or polymarket) and market_id': 1,
    }
    [found] = report.opportunities()
    assert found.relation.relation_id == 'r1'
    assert found.best.description == 'YES on A + NO on B'
    # Kalshi YES at 0.40 (30 contracts) + Polymarket NO at 0.45 (40): 30 contracts.
    assert found.depth.contracts == 30 and found.depth.stopped_by == 'book'
    payload = report.to_dict()
    assert payload['opportunities'][0]['depth']['net_edge'] == found.depth.net_edge
    assert 'assumptions' in payload


async def test_edge_that_is_gone_from_the_books_is_dropped(http) -> None:
    route_venues(
        http, kalshi_no_bid='0.4000', poly_bid='0.40'
    )  # books moved against us
    report = await scan_relations(RELATIONS)
    assert report.summary()['profitable_after_fees_top_of_book'] == 1
    assert report.opportunities() == []


async def test_top_of_book_only(http) -> None:
    route_venues(http)
    report = await scan_relations(RELATIONS, depth=False)
    assert [i.relation.relation_id for i in report.opportunities()] == ['r1']
    assert report.opportunities()[0].depth is None
    assert not http.calls(f'{venues.CLOB_API}/books')
