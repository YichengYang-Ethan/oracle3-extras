from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oracle3_extras.market.kairos import (
    MARKET_DATA_API,
    CandleRequest,
    KairosClient,
)

T0 = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
CANDLES = f'{MARKET_DATA_API}/v1/candles/batch'


def candle(minute: int, close: float) -> dict:
    start = (T0 + timedelta(minutes=minute)).isoformat()
    return {
        'bucket_start': start,
        'open': close,
        'high': close,
        'low': close,
        'close': close,
        'volume': 10,
        'timeframe_seconds': 60,
    }


def request(contract: str) -> CandleRequest:
    return CandleRequest('kalshi', contract, T0, T0 + timedelta(hours=1))


async def test_candles_are_batched_converted_and_errors_kept(http) -> None:
    def reply(req):
        import json

        items = json.loads(req.content)['requests']
        assert all(i['start'] == '2026-10-07T12:00:00Z' for i in items)
        return {
            'results': [
                {'index': n, 'candles': [candle(0, 55.0)]}
                if i['contract_id'] != 'bad'
                else {'index': n, 'candles': [], 'error': 'unknown contract'}
                for n, i in enumerate(items)
            ]
        }

    http.add('POST', CANDLES, reply)
    series = await KairosClient().candles(
        [request('A'), request('bad'), request('C')], batch_size=2, pause=0.5
    )
    assert [len(s.candles) for s in series] == [1, 0, 1]
    assert series[0].candles[0].close == 0.55
    assert series[1].error == 'unknown contract'
    assert len(http.requests) == 2 and http.waits == [0.5]


async def test_candles_stop_after_a_service_failure(http) -> None:
    http.add(
        'POST',
        CANDLES,
        {'results': [{'index': 0, 'candles': []}]},
        (503, {'detail': 'down'}),
    )
    series = await KairosClient().candles(
        [request('A'), request('B'), request('C')], batch_size=1, pause=0
    )
    assert series[0].error == ''
    assert series[1].error.startswith('not fetched') and series[2].error.startswith(
        'not fetched'
    )
    # One success, then the failed call and its four retries.
    assert len(http.requests) == 6


def test_candle_request_validation() -> None:
    with pytest.raises(ValueError):
        CandleRequest('kalshi', 'A', T0, T0 + timedelta(hours=1), timeframe=7)
    with pytest.raises(ValueError):
        CandleRequest('kalshi', 'A', T0.replace(tzinfo=None), T0.replace(tzinfo=None))


async def test_resolutions_batch_and_parse(http) -> None:
    def reply(req):
        ids = req.url.params['market_ids'].split(',')
        return {'resolutions': {i: 1 for i in ids if i.endswith('1')}}

    http.get(f'{MARKET_DATA_API}/v1/resolutions', reply)
    found = await KairosClient().resolutions('polymarket', (str(i) for i in range(250)))
    assert found['1'] == 1.0 and '2' not in found
    assert len(http.requests) == 2


async def test_marks_split_long_token_ids_below_the_url_limit(http) -> None:
    def reply(req):
        pairs = req.url.params['pairs'].split(',')
        return {
            'marks': [
                {'contract_id': m, 'token_id': t, 'price': 40}
                for m, t in (p.split(':') for p in pairs)
            ]
        }

    http.get(f'{MARKET_DATA_API}/v1/marks', reply)
    token = '7' * 77  # Predict.fun and Polymarket token ids are 77 digits
    tokens = [(f'{n}', f'{token}{n}') for n in range(250)]
    marks = await KairosClient().marks('predictfun', tokens)
    assert len(marks) == 250 and marks[('0', f'{token}0')] == 0.4
    sizes = [len(r.url.params['pairs'].split(',')) for r in http.requests]
    assert sizes == [100, 100, 50]
    assert max(len(str(r.url)) for r in http.requests) < 16_000


def test_url_batches_respect_the_character_budget() -> None:
    from oracle3_extras.market.kairos.client import _url_batches

    pairs = [('0x' + 'a' * 64, '1' * 77)] * 100  # Polymarket condition id + token
    assert [len(b) for b in _url_batches(pairs, 100, 15_000)] == [100]
    assert [len(b) for b in _url_batches(pairs, 100, 7_500)] == [50, 50]


async def test_candle_batches_follow_the_number_of_bars(http) -> None:
    sizes = []

    def reply(req):
        import json

        items = json.loads(req.content)['requests']
        sizes.append(len(items))
        return {'results': [{'index': n, 'candles': []} for n in range(len(items))]}

    http.add('POST', CANDLES, reply)
    six_hours = [
        CandleRequest('kalshi', f'M{n}', T0, T0 + timedelta(hours=6)) for n in range(60)
    ]
    hourly = [
        CandleRequest('kalshi', f'H{n}', T0, T0 + timedelta(hours=24), 3600)
        for n in range(450)
    ]
    await KairosClient().candles(six_hours, pause=0)
    assert sizes == [25, 25, 10]  # 360 one-minute bars each, 9,000 per call
    sizes.clear()
    await KairosClient().candles(hourly, pause=0)
    assert sizes == [200, 200, 50]


async def test_trade_metrics_per_market_and_failures_left_out(http) -> None:
    def reply(req):
        if req.url.params['contract_id'] == 'bad':
            return (400, {'error': {'code': 'invalid_request', 'message': 'no'}})
        assert req.url.params['window_seconds'] == '3600'
        return {
            'metrics': {
                'volume_usd': 120.5,
                'trade_count': 7,
                'coverage_pct': 80.0,
                'outcome_0_volume_usd': 100.0,
            }
        }

    http.get(f'{MARKET_DATA_API}/v1/trades/metrics', reply)
    found = await KairosClient().trade_metrics(
        [
            ('kalshi', 'KX-1'),
            ('polymarket', '55'),
            ('kalshi', 'bad'),
            ('kalshi', 'KX-1'),
        ],
        window_seconds=3600,
    )
    assert found == {
        ('kalshi', 'KX-1'): {
            'volume_usd': 120.5,
            'trade_count': 7.0,
            'coverage_pct': 80.0,
        },
        ('polymarket', '55'): {
            'volume_usd': 120.5,
            'trade_count': 7.0,
            'coverage_pct': 80.0,
        },
    }
    with pytest.raises(ValueError, match='window_seconds'):
        await KairosClient().trade_metrics([('kalshi', 'x')], window_seconds=60)
