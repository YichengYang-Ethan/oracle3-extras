from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from oracle3.market.relations import MarketRelation

from oracle3_extras.market.kairos import (
    MARKET_DATA_API,
    check_settlements,
    history_summary,
    price_history,
    settlement_summary,
)

START = datetime(2026, 10, 6, 23, tzinfo=timezone.utc)
NOW = START + timedelta(days=1)


def relation(
    rid: str, kind: str = 'same_event', start: datetime | None = START
) -> MarketRelation:
    return MarketRelation(
        relation_id=rid,
        market_a={'venue': 'kalshi', 'market_id': f'K{rid}', 'name': f'Kalshi {rid}'},
        market_b={
            'venue': 'polymarket',
            'market_id': f'P{rid}',
            'name': f'Poly {rid}',
            'game_start': start.isoformat() if start else '',
        },
        spread_type=kind,
    )


def bar(minute: int, price: float) -> dict:
    t = (START + timedelta(minutes=minute)).isoformat()
    return {
        'bucket_start': t,
        'open': price,
        'high': price,
        'low': price,
        'close': price,
        'volume': 1,
        'timeframe_seconds': 60,
    }


async def test_history_pairs_minutes_where_both_traded(http) -> None:
    seen = []

    def reply(req):
        items = json.loads(req.content)['requests']
        seen.extend(items)
        out = []
        for n, item in enumerate(items):
            if item['provider'] == 'kalshi':
                out.append(
                    {'index': n, 'candles': [bar(-30, 50), bar(10, 60), bar(20, 70)]}
                )
            else:
                out.append({'index': n, 'candles': [bar(-30, 49), bar(10, 55)]})
        return {'results': out}

    http.add('POST', f'{MARKET_DATA_API}/v1/candles/batch', reply)
    relations = [
        relation('a'),
        relation('b', 'complement'),
        relation('c', 'implication'),
        relation('d', start=None),
    ]
    histories = await price_history(relations, now=NOW)
    a, b, c, d = histories
    assert [round(g, 4) for g in a.gaps] == [0.01, 0.05]
    poly_outcomes = [i['outcome'] for i in seen if i['provider'] == 'polymarket']
    assert poly_outcomes == [0, 1]  # a complement compares Polymarket's second outcome
    assert c.problem.startswith('not a Kalshi') and d.problem == 'no start time'
    summary = history_summary(histories)
    assert summary['pairs_with_overlap'] == 2 and summary['minutes'] == 4
    assert (
        summary['before_start']['minutes'] == 2
        and summary['after_start']['minutes'] == 2
    )
    assert summary['problems'] == {
        'not a Kalshi–Polymarket same_event or complement relation': 1,
        'no start time': 1,
    }
    assert summary['widest'][0]['max_abs_gap'] == 0.05


async def test_future_events_are_not_fetched(http) -> None:
    [future] = await price_history(
        [relation('f', start=NOW + timedelta(hours=5))], now=NOW
    )
    assert future.problem == 'not started yet' and http.requests == []


async def test_settlements_compare_both_relation_types(http) -> None:
    http.get(
        f'{MARKET_DATA_API}/v1/resolutions',
        lambda req: {
            'resolutions': {'Ka': 1, 'Kb': 1, 'Kc': 0}
            if req.url.params['provider'] == 'kalshi'
            else {'Pa': 1, 'Pb': 0, 'Pc': 0.5}
        },
    )
    checks = await check_settlements(
        [relation('a'), relation('b', 'complement'), relation('c'), relation('d')]
    )
    assert [c.agrees for c in checks] == [True, True, False, None]
    summary = settlement_summary(checks)
    assert summary == {
        'checked': 4,
        'settled': 3,
        'pending': 1,
        'agree': 2,
        'disagree': 1,
        'agreement_rate': 0.6667,
        'mismatches': [checks[2].to_dict()],
    }
