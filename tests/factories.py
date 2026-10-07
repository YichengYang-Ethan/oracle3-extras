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
