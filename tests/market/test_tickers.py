from __future__ import annotations

import json

import pytest

from oracle3_extras import venues
from oracle3_extras.market.tickers import (
    Company,
    event_type,
    match_kalshi,
    match_polymarket,
    sp500,
    ticker_markets,
)

NVDA = Company('NVDA', 'Nvidia', ('Nvidia', 'NVIDIA'))
TXN = Company('TXN', 'Texas Instruments', ('Texas Instruments',))
GOOGL = Company('GOOGL', 'Alphabet Inc. (Class A)', ('Alphabet', 'Google'))
META = Company('META', 'Meta Platforms', ('Meta', 'Facebook'))
BX = Company('BX', 'Blackstone', ('Blackstone',))
COMPANIES = [NVDA, TXN, GOOGL, META, BX]


def poly_event(
    title: str,
    tags: tuple[str, ...] = ('finance',),
    slug: str = 'event',
    markets: list[dict] | None = None,
    volume: float = 1000.0,
) -> dict:
    return {
        'id': slug,
        'slug': slug,
        'title': title,
        'tags': [{'slug': t} for t in tags],
        'endDate': '2026-10-31T00:00:00Z',
        'volume24hr': volume,
        'markets': markets
        if markets is not None
        else [
            {
                'id': f'{slug}-1',
                'question': title,
                'groupItemTitle': '',
                'volume24hr': volume,
                'lastTradePrice': 0.62,
                'outcomePrices': '["0.62", "0.38"]',
            }
        ],
    }


def kalshi_event(title: str, ticker: str, category: str = 'Financials') -> dict:
    return {
        'event_ticker': ticker,
        'title': title,
        'sub_title': '',
        'category': category,
    }


# ── Matching ─────────────────────────────────────────────────────────────


def test_ticker_in_title_counts_only_in_finance_events() -> None:
    events = [
        poly_event('NVIDIA (NVDA) Up or Down on October 19?', slug='nvda'),
        poly_event('Florida (FL) Senate race margin?', tags=('politics',), slug='fl'),
        poly_event('S&P 500 (SPX) closes above ___ on October 19?', slug='spx'),
    ]
    [row] = match_polymarket(events, COMPANIES)
    assert (row.ticker, row.company, row.evidence, row.confidence) == (
        'NVDA',
        'Nvidia',
        'ticker in title',
        1.0,
    )
    assert row.event_type == 'price'
    assert row.url == 'https://polymarket.com/event/nvda'


def test_later_parenthesised_codes_are_metrics_not_tickers() -> None:
    events = [
        poly_event(
            'Will Blackstone (BX) Q3 total assets under management (AUM) be above __?'
        ),
        poly_event('Salesforce (CRM) Q3 current RPO growth (CC)?', slug='crm'),
    ]
    rows = match_polymarket(events, COMPANIES)
    assert sorted(r.ticker for r in rows) == ['BX', 'CRM']


def test_a_ticker_off_the_list_takes_its_name_from_the_title() -> None:
    events = [
        poly_event('Will Rocket Lab (RKLB) beat quarterly earnings?'),
        poly_event(
            'What will Robinhood Markets, Inc. (HOOD) hit Week of October 19 2026?'
        ),
    ]
    rows = {r.ticker: r for r in match_polymarket(events, COMPANIES)}
    assert (
        rows['RKLB'].company == 'Rocket Lab' and rows['RKLB'].event_type == 'earnings'
    )
    assert rows['HOOD'].company == 'Robinhood Markets, Inc.'
    # A given list keeps only its own tickers.
    only = match_polymarket(events, COMPANIES, only={'NVDA'})
    assert only == []


def test_company_names_match_whole_words_only() -> None:
    events = [
        kalshi_event('Nvidia headcount in fiscal 2027', 'A'),
        kalshi_event("Will Google's Gemini top the Arena?", 'B'),
        kalshi_event('Will the average minimum temperature in Texas be above 50?', 'C'),
        kalshi_event('Texas Instruments revenue in Q3', 'D'),
        kalshi_event('Meta-analysis of rate cuts published?', 'E'),
        kalshi_event('Metaverse land sales in 2026', 'F'),
    ]
    rows = match_kalshi(events, COMPANIES)
    assert [(r.event_id, r.ticker) for r in rows] == [
        ('A', 'NVDA'),
        ('B', 'GOOGL'),
        ('D', 'TXN'),
    ]
    assert all(r.evidence == 'company name' and r.confidence == 0.7 for r in rows)


def test_sports_and_platform_rankings_are_skipped() -> None:
    kalshi = [
        kalshi_event('Nvidia Classic winner?', 'S', category='Sports'),
        kalshi_event("Google's Year in Search 2026 Global - Movies: #1 search", 'Y'),
        kalshi_event('Top US Netflix Show this week?', 'N'),
    ]
    poly = [poly_event('#1 Most Searched Person on Google 2026?', tags=('tech',))]
    assert (
        match_kalshi(kalshi, [*COMPANIES, Company('NFLX', 'Netflix', ('Netflix',))])
        == []
    )
    assert match_polymarket(poly, COMPANIES) == []


def test_kalshi_mentions_category_is_a_mentions_market() -> None:
    [row] = match_kalshi(
        [kalshi_event('Nvidia Q3 call: AI factory?', 'M', category='Mentions')],
        COMPANIES,
    )
    assert row.event_type == 'mentions'


@pytest.mark.parametrize(
    ('title', 'kind'),
    [
        ('NVIDIA (NVDA) Up or Down on October 19?', 'price'),
        ('Meta’s Market Cap end of October 2026?', 'price'),
        ('Will Starbucks (SBUX) beat quarterly earnings?', 'earnings'),
        ('Starbucks total global stores in 2026', 'kpi'),
        ('NVIDIA H200 · Average hourly price in August 2027', 'kpi'),
        ('Walmart Foot Traffic in October', 'kpi'),
        ("McDonald's Big Mac price in October", 'kpi'),
        ('What will Google say during their next earnings call?', 'mentions'),
        ('Will Meta acquire Instinct before 2028?', 'deal'),
        ('DOJ fines Nvidia over Groq deal in 2026?', 'legal'),
        ('Courts consider Amazon a monopoly?', 'legal'),
        ('Who will be the next CEO of JPMorgan Chase?', 'leadership'),
        ('Apple Vision Pro 2 released before 2027?', 'product'),
        (
            'Will United Airlines shift its headquarters to Denver CO before 2027?',
            'other',
        ),
    ],
)
def test_event_types(title: str, kind: str) -> None:
    assert event_type(title) == kind


def test_bundled_sp500_list() -> None:
    companies = sp500()
    tickers = [c.ticker for c in companies]
    assert len(companies) == 503 and len(set(tickers)) == 503
    names = {n for c in companies for n in c.names}
    # Words that are also places, people or everyday words never stand alone.
    assert not names & {'Texas', 'Target', 'Visa', 'Delta', 'Abbott', 'Hilton', 'Host'}
    by_ticker = {c.ticker: c for c in companies}
    assert by_ticker['GOOG'].names == () and 'Google' in by_ticker['GOOGL'].names
    assert 'Texas Instruments' in by_ticker['TXN'].names


# ── Fetching ─────────────────────────────────────────────────────────────


def route(http, *, kalshi_markets=None) -> None:
    def gamma(request):
        if request.url.params['tag_slug'] != 'finance':
            return []
        return [
            poly_event(
                'NVIDIA (NVDA) closes above ___ on October 23?',
                slug='nvda-above',
                volume=5000.0,
                markets=[
                    {
                        'id': '11',
                        'groupItemTitle': '$180',
                        'volume24hr': 1000,
                        'lastTradePrice': 0.7,
                    },
                    {
                        'id': '12',
                        'groupItemTitle': '$190',
                        'volume24hr': 4000,
                        'lastTradePrice': 0.35,
                    },
                    {'id': '13', 'groupItemTitle': '$200', 'closed': True},
                ],
            ),
            poly_event('Will Blackstone (BX) beat quarterly earnings?', slug='bx-beat'),
        ]

    http.get(f'{venues.GAMMA_API}/events', gamma)
    http.get(
        f'{venues.KALSHI_API}/events',
        {
            'events': [kalshi_event('Nvidia headcount in fiscal 2027', 'KXNVDAA-27')],
            'cursor': 'next',
        },
        {
            'events': [
                kalshi_event(
                    'NVIDIA H200 · Average hourly price in August 2027', 'KXH200-27'
                ),
                kalshi_event('Cardinals at Rams', 'KXNFL-1', category='Sports'),
            ],
            'cursor': '',
        },
    )
    http.get(
        f'{venues.KALSHI_API}/markets',
        kalshi_markets
        or (
            lambda request: {
                'markets': [
                    {
                        'ticker': f"{request.url.params['event_ticker']}-A",
                        'yes_sub_title': 'Above 40,000',
                        'status': 'active',
                        'last_price_dollars': '0.0000',
                        'yes_bid_dollars': '0.4000',
                        'yes_ask_dollars': '0.5000',
                        'volume_24h_fp': '120.00',
                        'close_time': '2027-02-01T00:00:00Z',
                    },
                    {
                        'ticker': f"{request.url.params['event_ticker']}-B",
                        'yes_sub_title': 'Above 50,000',
                        'status': 'active',
                        'last_price_dollars': '0.0000',
                        'yes_bid_dollars': '0.0000',
                        'yes_ask_dollars': '1.0000',
                        'volume_24h_fp': '0.00',
                        'close_time': '2027-02-01T00:00:00Z',
                    },
                ]
            }
        ),
    )


async def test_ticker_markets_reads_both_venues(http) -> None:
    route(http)
    result = await ticker_markets()
    rows = result.for_ticker('NVDA')
    assert [(r.venue, r.event_id) for r in rows] == [
        ('polymarket', 'nvda-above'),
        ('kalshi', 'KXNVDAA-27'),
        ('kalshi', 'KXH200-27'),
    ]
    poly, kalshi = rows[0], rows[1]
    # The most traded open market, and the event's dollar volume.
    assert (poly.top_market, poly.top_market_id, poly.yes_price) == ('$190', '12', 0.35)
    assert poly.markets == 2 and poly.volume_24h_usd == 5000.0
    # Kalshi: no last trade, so the midpoint of the two-sided book.
    assert (kalshi.top_market, kalshi.yes_price) == ('Above 40,000', 0.45)
    assert kalshi.volume_24h_contracts == 120.0 and kalshi.markets == 2
    assert kalshi.closes == '2027-02-01T00:00:00Z'
    summary = result.summary()
    assert summary['events_searched'] == {'polymarket': 2, 'kalshi': 3}
    assert summary['by_venue'] == {'kalshi': 2, 'polymarket': 2}
    assert summary['top_tickers'] == {'NVDA': 3, 'BX': 1}
    # Only the matched Kalshi events cost a markets request each.
    asked = {
        r.url.params['event_ticker'] for r in http.calls(f'{venues.KALSHI_API}/markets')
    }
    assert asked == {'KXNVDAA-27', 'KXH200-27'}


async def test_filters_by_companies_type_and_confidence(http) -> None:
    route(http)
    result = await ticker_markets(
        [Company('NVDA', 'Nvidia', ('Nvidia', 'NVIDIA'))], types=['kpi'], prices=False
    )
    assert [r.event_id for r in result.events] == ['KXNVDAA-27', 'KXH200-27']
    assert not http.calls(f'{venues.KALSHI_API}/markets')
    sure = await ticker_markets(min_confidence=1.0, venues=('polymarket',))
    assert {r.ticker for r in sure.events} == {'NVDA', 'BX'}


async def test_an_empty_book_has_no_price(http) -> None:
    route(
        http,
        kalshi_markets={
            'markets': [
                {
                    'ticker': 'X',
                    'status': 'active',
                    'last_price_dollars': '0.0000',
                    'yes_bid_dollars': '0.0000',
                    'yes_ask_dollars': '1.0000',
                    'volume_24h_fp': '0',
                }
            ]
        },
    )
    result = await ticker_markets(venues=('kalshi',))
    assert {r.yes_price for r in result.events} == {None}


def test_rows_serialise() -> None:
    [row] = match_polymarket([poly_event('NVIDIA (NVDA) Up or Down?')], COMPANIES)
    assert json.loads(json.dumps(row.to_dict()))['ticker'] == 'NVDA'
