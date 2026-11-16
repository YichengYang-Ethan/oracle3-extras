"""Venue payloads shaped like the real Kalshi, Polymarket and Kairos APIs."""

from __future__ import annotations

import json
from typing import Any


def kalshi_market(
    ticker: str,
    yes: str,
    *,
    title: str | None = None,
    status: str = 'active',
    yes_bid: str = '0.4800',
    yes_ask: str = '0.5000',
    occurrence: str = '2026-10-11T02:00:00Z',
    rules: str = 'If X wins the X vs Y game originally scheduled for Oct 10, 2026, then the market resolves to Yes.',
    **extra: Any,
) -> dict[str, Any]:
    no_bid = f'{1 - float(yes_ask):.4f}'
    no_ask = f'{1 - float(yes_bid):.4f}'
    return {
        'ticker': ticker,
        'event_ticker': ticker.rsplit('-', 1)[0],
        'title': title if title is not None else f'{yes} wins',
        'yes_sub_title': yes,
        'no_sub_title': yes,
        'status': status,
        'yes_bid_dollars': yes_bid,
        'yes_ask_dollars': yes_ask,
        'no_bid_dollars': no_bid,
        'no_ask_dollars': no_ask,
        'occurrence_datetime': occurrence,
        'expected_expiration_time': occurrence,
        'close_time': '2026-10-12T23:00:00Z',
        'rules_primary': rules,
        'strike_type': 'structured',
        'floor_strike': None,
        'market_type': 'binary',
        **extra,
    }


def gamma_market(
    market_id: str,
    question: str,
    outcomes: list[str],
    *,
    slug: str,
    start: str = '2026-10-10 23:00:00+00',
    sports_type: str | None = 'moneyline',
    line: float | None = None,
    group: str | None = None,
    best_bid: float = 0.48,
    best_ask: float = 0.50,
    tokens: tuple[str, str] = ('tok-a', 'tok-b'),
    fee_rate: float = 0.05,
    event_title: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        'id': market_id,
        'question': question,
        'slug': slug,
        'outcomes': json.dumps(outcomes),
        'clobTokenIds': json.dumps(list(tokens)),
        'conditionId': f'0xcond{market_id}',
        'sportsMarketType': sports_type,
        'line': line,
        'groupItemTitle': group,
        'gameStartTime': start,
        'endDate': start.replace(' ', 'T').replace('+00', 'Z'),
        'bestBid': best_bid,
        'bestAsk': best_ask,
        'active': True,
        'closed': False,
        'acceptingOrders': True,
        'feesEnabled': True,
        'feeSchedule': {
            'exponent': 1,
            'rate': fee_rate,
            'takerOnly': True,
            'rebateRate': 0.15,
        },
        'feeType': 'sports_fees_v3',
        'events': [{'title': event_title or question}],
        **extra,
    }


def kairos_side(
    provider: str, market_id: str, title: str, provider_id: int
) -> dict[str, Any]:
    return {
        'provider_id': provider_id,
        'provider': provider,
        'market_id': market_id,
        'title': title,
        'ticker': market_id if provider == 'kalshi' else f'0xcond{market_id}',
        'image': None,
        'icon': None,
        'expires_at': None,
        'category': 'Sports',
    }


def kairos_pair(
    ticker: str,
    kalshi_title: str,
    poly_id: str,
    poly_title: str,
    similarity: float = 1.0,
) -> dict[str, Any]:
    return {
        'a': kairos_side('kalshi', ticker, kalshi_title, 1),
        'b': kairos_side('polymarket', poly_id, poly_title, 2),
        'similarity': similarity,
        'updated_at': '2026-10-07T05:38:57.617000',
    }


def kairos_page(
    pairs: list[dict[str, Any]], *, next_cursor: str | None = None, version: str = 'v1'
) -> dict[str, Any]:
    return {
        'pairs': pairs,
        'count': len(pairs),
        'limit': 1000,
        'offset': 0,
        'has_more': next_cursor is not None,
        'next_cursor': next_cursor,
        'catalog_version': version,
    }


def kairos_md_market(
    market_id: str,
    title: str,
    outcomes: list[str],
    *,
    event_id: str = '',
    status: str = 'open',
    category: str = 'Sports',
    expires_at: str = '2026-10-10T23:00:00Z',
    yes_sub_title: str = '',
    fee_bps: int | None = None,
) -> dict[str, Any]:
    """One market as Kairos's Market Data API returns it (``POST /v1/markets/batch``)."""
    listed = [
        {'outcome': o, 'normalized_outcome': o.lower(), 'token_id': f'{market_id}-t{i}'}
        for i, o in enumerate(outcomes)
    ]
    return {
        'market_id': market_id,
        'condition_id': f'0xcond{market_id}',
        'event_id': event_id,
        'title': title,
        'status': status,
        'category': category,
        'end_date': None,
        'taker_base_fee_bps': fee_bps,
        'tick_size': 0.01,
        'neg_risk': False,
        'outcomes': listed,
        'raw': {
            'name': title,
            'expires_at': expires_at,
            'yes_sub_title': yes_sub_title,
        },
    }


def kairos_any_pair(
    a: tuple[str, str, str], b: tuple[str, str, str], similarity: float = 1.0
) -> dict[str, Any]:
    """A catalog pair between any two venues: each side is (provider, market id, title)."""
    ids = {'kalshi': 1, 'polymarket': 2, 'predictfun': 3, 'hyperliquid': 9}
    return {
        'a': kairos_side(a[0], a[1], a[2], ids[a[0]]),
        'b': kairos_side(b[0], b[1], b[2], ids[b[0]]),
        'similarity': similarity,
        'updated_at': '2026-10-07T05:38:57.617000',
    }


#: Kairos's numeric provider ids, as ``POST /markets/details`` reports them.
KAIROS_PROVIDER_IDS = {'kalshi': 1, 'polymarket': 2, 'predictfun': 8, 'hyperliquid': 9}


def kairos_leg(provider: str, market_id: str) -> dict[str, Any]:
    """One venue of a Kairos link (``GET /market-links/featured``).

    A Polymarket leg's ``marketId`` is the condition id and its ``streamKey``
    the numeric Gamma id; the other venues use their own id for both.
    """
    if provider == 'polymarket':
        return {
            'provider': 'polymarket',
            'marketId': f'0xcond{market_id}',
            'streamKey': market_id,
            'tokenIdYes': f'{market_id}-yes',
            'tokenIdNo': f'{market_id}-no',
            'executable': True,
        }
    return {
        'provider': provider,
        'marketId': market_id,
        'streamKey': market_id,
        'tokenIdYes': f'{market_id}::0',
        'tokenIdNo': f'{market_id}::1',
        'executable': provider != 'kalshi',
    }


def kairos_link(
    *legs: tuple[str, str],
    link_id: str = 'link-1',
    title: str = '',
    end_time: str | None = None,
) -> dict[str, Any]:
    """An approved Kairos link: one contract on two to four venues."""
    return {
        'id': link_id,
        'title': title,
        'image': None,
        'league': None,
        'sport': None,
        'startTime': None,
        'endTime': end_time,
        'venues': [kairos_leg(provider, market_id) for provider, market_id in legs],
        'executableVenues': [p for p, _ in legs if p != 'kalshi'],
        'rank': 1.0,
        'sideLabels': {'yes': 'Yes', 'no': 'No'},
    }


def kairos_links_page(
    links: list[dict[str, Any]], *, next_cursor: str | None = None
) -> dict[str, Any]:
    return {'links': links, 'nextCursor': next_cursor}


def link_from_pair(pair: dict[str, Any]) -> dict[str, Any]:
    """The approved link holding both markets of an old-catalog pair."""
    a, b = pair['a'], pair['b']
    return kairos_link(
        (a['provider'], a['market_id']),
        (b['provider'], b['market_id']),
        link_id=f"link-{a['market_id']}-{b['market_id']}",
        title=a.get('title') or '',
    )


def kairos_details(*pairs: dict[str, Any]) -> dict[str, Any]:
    """``POST /markets/details`` naming and categorising both sides of each pair."""
    rows = []
    for pair in pairs:
        for side in (pair['a'], pair['b']):
            if 'title' not in side:
                continue  # a market Kairos has no details for
            provider = side['provider']
            rows.append(
                {
                    'market_id': side['market_id'],
                    'provider_id': KAIROS_PROVIDER_IDS[provider],
                    'event_id': None,
                    'name': side['title'],
                    'category': side.get('category'),
                    'status': 'open',
                    'condition_id': (
                        f"0xcond{side['market_id']}"
                        if provider == 'polymarket'
                        else None
                    ),
                    'token_id': None,
                    'token_ids': [],
                    'outcomes': [],
                }
            )
    return {'markets': rows}


def serve_kairos_catalog(http: Any, *pairs: dict[str, Any]) -> None:
    """Answer the catalog walk with one link per pair, and the details of each market."""
    from oracle3_extras.market.kairos import DATA_API

    http.get(
        f'{DATA_API}/market-links/featured',
        kairos_links_page([link_from_pair(p) for p in pairs]),
    )
    http.add('POST', f'{DATA_API}/markets/details', kairos_details(*pairs))
