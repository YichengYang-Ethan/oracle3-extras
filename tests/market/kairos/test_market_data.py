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
    assert (
        len(http.requests) == 5
    )  # one success, then the failed call and its three retries


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
