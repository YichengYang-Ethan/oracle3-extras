"""Contract tests against the real services: run with ``pytest --live``.

They catch schema changes in the Kairos, Kalshi and Polymarket APIs that the
offline tests cannot see.
"""

from __future__ import annotations

import json

import pytest

from oracle3_extras import venues
from oracle3_extras.arbitrage import scan_relations
from oracle3_extras.market.kairos import KairosClient, kairos_relations

pytestmark = pytest.mark.live


async def test_kairos_catalog_aligns_and_scans() -> None:
    result = await kairos_relations()
    summary = result.summary()
    assert summary['kalshi_polymarket'] > 0
    assert summary['aligned'] >= 0.8 * summary['kalshi_polymarket']
    report = await scan_relations(result.relations[:200], depth=False)
    assert report.summary()['quoted'] > 0


async def test_kairos_resolutions_match_the_venues() -> None:
    """Kairos's settled fractions equal Kalshi's result and Polymarket's payout."""
    from oracle3_extras._http import new_client, request_json

    async with new_client() as client:
        settled = await request_json(
            client,
            'GET',
            f'{venues.KALSHI_API}/markets',
            params={'series_ticker': 'KXNHLGAME', 'status': 'settled', 'limit': 10},
        )
        closed = await request_json(
            client,
            'GET',
            f'{venues.GAMMA_API}/markets',
            params={
                'closed': 'true',
                'limit': 10,
                'tag_id': 1,
                'order': 'endDate',
                'ascending': 'false',
            },
        )
    tickers = {
        m['ticker']: m['result']
        for m in settled['markets']
        if m.get('result') in ('yes', 'no')
    }
    kairos = KairosClient()
    k = await kairos.resolutions('kalshi', tickers)
    assert k, 'Kairos resolved none of the settled Kalshi markets'
    for ticker, fraction in k.items():
        assert fraction == (1.0 if tickers[ticker] == 'yes' else 0.0)
    payouts = {
        str(m['id']): float(json.loads(m['outcomePrices'])[0])
        for m in closed
        if m.get('outcomePrices')
    }
    p = await kairos.resolutions('polymarket', payouts)
    for market_id, fraction in p.items():
        assert abs(fraction - payouts[market_id]) < 1e-9
