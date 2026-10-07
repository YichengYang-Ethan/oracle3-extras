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
        'markets need venue and market_id': 1,
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


# ── Predict.fun legs (through Kairos) ────────────────────────────────────

from oracle3_extras.market.kairos import (  # noqa: E402
    EXECUTION_API,
    MARKET_DATA_API,
    KairosClient,
    KairosCredentials,
)
from tests.factories import kairos_md_market  # noqa: E402

PM_TWIN = gamma_market(
    '11',
    'Texans vs. Titans',
    ['Texans', 'Titans'],
    slug='nfl-hou-ten-2026-10-11',
    best_bid=0.60,
    best_ask=0.62,
    tokens=('pm-a', 'pm-b'),
)
PF_TWIN = kairos_md_market(
    'pf11',
    'Texans vs. Titans',
    ['HOU', 'TEN'],
    event_id='nfl-hou-ten-2026-10-11',
    fee_bps=200,
)
TWIN = rel(
    'twin',
    {'venue': 'polymarket', 'market_id': '11'},
    {'venue': 'predictfun', 'market_id': 'pf11'},
)
HL = rel(
    'hl',
    {'venue': 'polymarket', 'market_id': '11'},
    {'venue': 'hyperliquid', 'market_id': '8764'},
)


def route_twin(http, *, fee_quote=None) -> None:
    http.get(f'{venues.GAMMA_API}/markets', [PM_TWIN])
    http.add(
        'POST',
        f'{MARKET_DATA_API}/v1/markets/batch',
        {'markets': {'pf11': PF_TWIN}, 'misses': []},
    )
    # Last trades: Texans 0.30 on Predict.fun, so YES on Polymarket's Titans side + Texans on Predict.fun looks cheap.
    http.get(
        f'{MARKET_DATA_API}/v1/marks',
        {
            'marks': [
                {'contract_id': 'pf11', 'token_id': 'pf11-t0', 'price': 30},
                {'contract_id': 'pf11', 'token_id': 'pf11-t1', 'price': 70},
            ]
        },
    )
    http.add(
        'POST',
        f'{venues.CLOB_API}/books',
        [
            {'asset_id': 'pm-a', 'asks': [{'price': '0.62', 'size': '500'}]},
            {'asset_id': 'pm-b', 'asks': [{'price': '0.40', 'size': '500'}]},
        ],
    )
    if fee_quote is not None:
        http.get(f'{EXECUTION_API}/orders/fee-quote', fee_quote)


async def test_predict_fun_without_a_key_stays_unconfirmed(http) -> None:
    route_twin(http)
    report = await scan_relations([TWIN, HL], client=KairosClient(credentials=None))
    twin, hl = report.items
    assert twin.indicative and twin.best.description == 'NO on A + YES on B'
    assert twin.skipped.startswith('unconfirmed') and not twin.opportunity
    assert hl.skipped == 'no live quotes for Hyperliquid yet'
    assert report.summary()['priced_from_last_trades'] == 1


async def test_predict_fun_legs_are_sized_with_kairos_fee_quotes(http) -> None:
    def quote(request):
        size = float(request.url.params['quantity'])
        assert request.url.params['token_id'] == 'pf11-t0'
        assert request.headers['X-Api-Key'] == 'k'
        if size > 50:
            return {
                'pricing_unavailable': False,
                'sufficient_liquidity': False,
                'avg_price_usdc': '0.31',
                'filled_size': '50',
                'exchange_fee_usdc': '0',
                'platform_fee_usdc': '0',
            }
        return {
            'pricing_unavailable': False,
            'sufficient_liquidity': True,
            'avg_price_usdc': '0.31',
            'filled_size': str(size),
            'exchange_fee_usdc': f'{0.02 * 0.31 * size:.6f}',
            'platform_fee_usdc': '0.01',
        }

    route_twin(http, fee_quote=quote)
    client = KairosClient(credentials=KairosCredentials('id', 'k', 's'))
    report = await scan_relations([TWIN], client=client)
    [item] = report.items
    assert item.opportunity
    assert item.depth.contracts == 50 and item.depth.stopped_by == 'book'
    pf_leg = [leg for leg in item.depth.legs if leg['venue'] == 'predictfun'][0]
    assert pf_leg['average_price'] == 0.31
    # 50 × (1 − 0.40 − 0.31) minus both venues' fees
    assert item.depth.net_edge == pytest.approx(50 * 0.29 - item.depth.fees)


async def test_untraded_predict_fun_market_is_skipped_not_quoted(http) -> None:
    route_twin(http)
    http.routes[('GET', f'{MARKET_DATA_API}/v1/marks')] = [{'marks': []}]
    report = await scan_relations([TWIN], client=KairosClient(credentials=None))
    [item] = report.items
    assert item.check is None and item.skipped == 'no Predict.fun trades yet'


async def test_a_venue_outage_skips_its_relations_and_keeps_scanning(http) -> None:
    route_twin(http)
    http.routes[('GET', f'{MARKET_DATA_API}/v1/marks')] = [(503, {'detail': 'down'})]
    report = await scan_relations([TWIN], client=KairosClient(credentials=None))
    [item] = report.items
    assert item.skipped == 'predictfun data unavailable'


async def test_only_the_largest_last_trade_edges_are_confirmed(http) -> None:
    route_twin(http, fee_quote={'pricing_unavailable': True})
    client = KairosClient(credentials=KairosCredentials('id', 'k', 's'))
    report = await scan_relations([TWIN], client=client, max_confirm=0)
    [item] = report.items
    assert item.skipped == 'unconfirmed: beyond the 0 largest last-trade edges'
    assert not [r for r in http.requests if 'fee-quote' in str(r.url)]
