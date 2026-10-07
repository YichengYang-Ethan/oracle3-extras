from __future__ import annotations

from decimal import Decimal

import pytest
from oracle3.fees import KalshiSchedule, PolymarketSchedule, UnsupportedFeeSchedule

from oracle3_extras import venues
from tests.factories import gamma_market, kalshi_market

KALSHI_MARKETS = f'{venues.KALSHI_API}/markets'
GAMMA_MARKETS = f'{venues.GAMMA_API}/markets'


async def test_kalshi_markets_batches_tickers(http) -> None:
    tickers = [f'KXT-26OCT10AB-{i}' for i in range(150)]

    def reply(request):
        asked = request.url.params['tickers'].split(',')
        assert request.url.params['limit'] == str(len(asked))
        return {'markets': [kalshi_market(t, 'A') for t in asked]}

    http.get(KALSHI_MARKETS, reply)
    found = await venues.kalshi_markets(tickers + tickers[:3])
    assert len(found) == 150
    assert len(http.requests) == 2  # 100 + 50, duplicates dropped


async def test_polymarket_markets_sends_ids_and_limit(http) -> None:
    def reply(request):
        ids = request.url.params.get_list('id')
        assert request.url.params['limit'] == str(len(ids))
        return [gamma_market(i, 'Q', ['Yes', 'No'], slug='s') for i in ids]

    http.get(GAMMA_MARKETS, reply)
    found = await venues.polymarket_markets(str(i) for i in range(60))
    assert sorted(found, key=int) == [str(i) for i in range(60)]
    assert len(http.requests) == 2


def test_kalshi_book_turns_bids_into_asks() -> None:
    book = venues.kalshi_book(
        'T',
        {
            'orderbook_fp': {
                'yes_dollars': [['0.4700', '97.00'], ['0.5200', '43.00']],
                'no_dollars': [['0.4000', '100.00'], ['0.4500', '31.00']],
            }
        },
    )
    # The best YES ask is 1 - best NO bid.
    assert book.yes_asks == [(0.55, 31.0), (0.6, 100.0)]
    assert book.no_asks == [(0.48, 43.0), (0.53, 97.0)]


async def test_kalshi_books_repeat_the_tickers_param(http) -> None:
    def reply(request):
        asked = request.url.params.get_list('tickers')
        return {
            'orderbooks': [
                {
                    'ticker': t,
                    'orderbook_fp': {'yes_dollars': [['0.40', '5']], 'no_dollars': []},
                }
                for t in asked
            ]
        }

    http.get(f'{venues.KALSHI_API}/markets/orderbooks', reply)
    books = await venues.kalshi_books(['A', 'B'])
    assert set(books) == {'A', 'B'}
    assert books['A'].no_asks == [(0.6, 5.0)]


async def test_polymarket_books_sorts_asks(http) -> None:
    http.add(
        'POST',
        f'{venues.CLOB_API}/books',
        [
            {
                'asset_id': 'tok-a',
                'asks': [
                    {'price': '0.99', 'size': '5'},
                    {'price': '0.52', 'size': '10'},
                ],
            },
            {'asset_id': 'tok-b', 'asks': [{'price': '0.50', 'size': '7'}]},
        ],
    )
    asks = await venues.polymarket_books(['tok-a', 'tok-b'])
    assert asks['tok-a'] == [(0.52, 10.0), (0.99, 5.0)]


def test_kalshi_quote_uses_series_schedule() -> None:
    quote = venues.kalshi_quote(
        kalshi_market('T', 'A', yes_bid='0.4000', yes_ask='0.4200'),
        {'fee_type': 'quadratic_with_maker_fees', 'fee_multiplier': 1},
    )
    assert (quote.yes_bid, quote.yes_ask, quote.no_ask) == (0.40, 0.42, 0.60)
    assert isinstance(quote.schedule, KalshiSchedule) and quote.schedule.maker_fees


def test_kalshi_quote_rejects_combo_series() -> None:
    with pytest.raises(UnsupportedFeeSchedule):
        venues.kalshi_quote(kalshi_market('T', 'A'), {'fee_type': 'quadratic_combo'})


def test_polymarket_quote_reads_first_outcome_and_fee_rate() -> None:
    quote = venues.polymarket_quote(
        gamma_market(
            '9', 'Q', ['A', 'B'], slug='s', best_bid=0.53, best_ask=0.55, fee_rate=0.03
        )
    )
    assert (quote.yes_bid, quote.yes_ask) == (0.53, 0.55)
    assert quote.ask('no') == 0.47  # mirrored from the YES bid
    assert isinstance(quote.schedule, PolymarketSchedule)
    assert quote.schedule.rate == Decimal('0.03')


def test_prices_outside_zero_one_are_dropped() -> None:
    quote = venues.polymarket_quote(
        gamma_market('9', 'Q', ['A', 'B'], slug='s', best_bid=0.0, best_ask=1.0)
    )
    assert quote.yes_bid is None and quote.yes_ask is None
