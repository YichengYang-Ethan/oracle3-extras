from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from oracle3.market.relations import MarketRelation

from oracle3_extras.market.kairos import (
    MARKET_DATA_API,
    check_settlements,
    history_summary,
    price_history,
    recent_history,
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
    assert c.problem.startswith('not a same_event') and d.problem == 'no start time'
    summary = history_summary(histories)
    assert summary['pairs_with_overlap'] == 2 and summary['minutes'] == 4
    assert (
        summary['before_start']['minutes'] == 2
        and summary['after_start']['minutes'] == 2
    )
    assert summary['problems'] == {
        'not a same_event or complement relation Kairos can chart': 1,
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


async def test_recent_history_stops_at_the_event_and_skips_started_games(http) -> None:
    seen = []

    def reply(req):
        items = json.loads(req.content)['requests']
        seen.extend(items)
        hour = {
            'bucket_start': (NOW - timedelta(hours=3)).isoformat(),
            'open': 40,
            'high': 40,
            'low': 40,
            'close': 40 if items[0]['provider'] == 'kalshi' else 43,
            'volume': 1,
            'timeframe_seconds': 3600,
        }
        return {
            'results': [
                {
                    'index': n,
                    'candles': [
                        {**hour, 'close': 40 if i['provider'] == 'kalshi' else 43}
                    ],
                }
                for n, i in enumerate(items)
            ]
        }

    http.add('POST', f'{MARKET_DATA_API}/v1/candles/batch', reply)
    long_dated = relation('long', start=NOW + timedelta(days=80))
    tonight = relation('tonight', start=NOW - timedelta(hours=2))
    yesterday = relation('old', start=NOW - timedelta(hours=30))
    histories = await recent_history([long_dated, tonight, yesterday], now=NOW)
    long_h, tonight_h, old_h = histories
    assert [round(g, 4) for g in long_h.gaps] == [-0.03]
    assert old_h.problem == 'event started before the window'
    ends = {i['contract_id']: i['end'] for i in seen}
    assert ends['Klong'] == '2026-10-07T23:00:00Z'  # now
    assert ends['Ktonight'] == '2026-10-07T21:00:00Z'  # cut at the game's start
    assert {i['timeframe_seconds'] for i in seen} == {3600}
    summary = history_summary(histories)
    assert summary['minutes'] == 2 and summary['median_abs_gap'] == 0.03


async def test_one_venue_failing_leaves_its_side_unresolved(http) -> None:
    http.get(
        f'{MARKET_DATA_API}/v1/resolutions',
        lambda req: {'resolutions': {'Ka': 1}}
        if req.url.params['provider'] == 'kalshi'
        else (503, {'detail': 'down'}),
    )
    [check] = await check_settlements([relation('a')])
    assert check.first == 1 and check.second is None and check.agrees is None


def test_old_settlement_attribute_names_warn() -> None:
    import pytest

    from oracle3_extras.market.kairos import SettlementCheck

    check = SettlementCheck(relation('a'), first=1.0, second=0.0)
    with pytest.warns(FutureWarning, match='use SettlementCheck.first'):
        assert check.kalshi == 1.0
    with pytest.warns(FutureWarning, match='use SettlementCheck.second'):
        assert check.polymarket == 0.0
