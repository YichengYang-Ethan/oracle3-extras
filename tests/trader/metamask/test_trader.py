from __future__ import annotations

from decimal import Decimal

import pytest
from oracle3.data.market_data_manager import MarketDataManager
from oracle3.position.position_manager import PositionManager
from oracle3.risk.risk_manager import NoRiskManager
from oracle3.ticker.ticker import CashTicker, PolyMarketTicker
from oracle3.trader.types import OrderFailureReason, OrderStatus, TradeSide

from oracle3_extras.trader.metamask import (
    AgentWalletTrader,
    GeoblockedError,
    MainnetNotAllowedError,
)
from tests.trader.metamask.support import err, ok

TICKER = PolyMarketTicker(symbol='T1', name='Fed cut?', token_id='T1')


def balance_reply(dollars: str):
    """The CLOB reports collateral in 6-decimal base units."""
    base_units = str(int(Decimal(dollars) * 1_000_000))
    return ok(
        'balance',
        {'assetType': 'COLLATERAL', 'balanceAllowance': {'balance': base_units}},
    )


def place_reply(status='matched', success=True, making='5.5', taking='10'):
    return ok(
        'place',
        {
            'response': {
                'orderId': '0xabc',
                'status': status,
                'success': success,
                'makingAmount': making,
                'takingAmount': taking,
            }
        },
    )


def make_trader(
    fake_mm, *, mode='testnet', balance='50', **kwargs
) -> AgentWalletTrader:
    fake_mm.respond('predict mode', ok('mode', {'mode': mode}))
    fake_mm.respond('predict balance', balance_reply(balance))
    fake_mm.help('predict place', 'Usage: mm predict place')
    return AgentWalletTrader(
        MarketDataManager(),
        NoRiskManager(),
        PositionManager(),
        client=fake_mm.client(),
        **kwargs,
    )


async def test_preflight_testnet_loads_balance(fake_mm) -> None:
    trader = make_trader(fake_mm)
    summary = await trader.preflight()
    assert summary['mode'] == 'testnet'
    assert summary['attribution'] is True
    cash = trader.position_manager.get_position(CashTicker.POLYMARKET_USDC)
    assert cash.quantity == Decimal('50')
    assert fake_mm.calls('predict geoblock') == []


async def test_preflight_refuses_mainnet_without_opt_in(fake_mm) -> None:
    trader = make_trader(fake_mm, mode='mainnet')
    with pytest.raises(MainnetNotAllowedError):
        await trader.preflight()


async def test_preflight_mainnet_checks_geoblock(fake_mm) -> None:
    trader = make_trader(fake_mm, mode='mainnet', allow_mainnet=True)
    fake_mm.respond(
        'predict geoblock', ok('geoblock', {'blocked': True, 'country': 'US'})
    )
    with pytest.raises(GeoblockedError):
        await trader.preflight()


async def test_place_before_preflight_is_rejected(fake_mm) -> None:
    trader = make_trader(fake_mm)
    result = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )
    assert result.failure_reason == OrderFailureReason.TRADING_DISABLED
    assert fake_mm.calls('predict place') == []


async def test_buy_fok_fill_updates_positions_and_cash(fake_mm) -> None:
    trader = make_trader(fake_mm)
    fake_mm.respond('predict balance', [balance_reply('50'), balance_reply('44.5')])
    await trader.preflight()
    fake_mm.respond('predict place', place_reply())

    result = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )

    assert result.failure_reason is None
    order = result.order
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal('10')
    assert order.average_price == Decimal('0.55')
    assert trader.position_manager.get_position(TICKER).quantity == Decimal('10')
    cash = trader.position_manager.get_position(CashTicker.POLYMARKET_USDC)
    assert cash.quantity == Decimal('44.5')
    argv = fake_mm.calls('predict place')[-1]['argv']
    assert argv[argv.index('--token-id') + 1] == 'T1'
    assert argv[argv.index('--price') + 1] == '0.56'


async def test_amounts_in_base_units_are_scaled(fake_mm) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond('predict place', place_reply(making='5500000', taking='10000000'))
    result = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )
    assert result.order.filled_quantity == Decimal('10')
    assert result.order.average_price == Decimal('0.55')


async def test_sell_fill_uses_making_as_shares(fake_mm) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond('predict place', place_reply())
    await trader.place_order(TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10'))
    fake_mm.respond('predict place', place_reply(making='10', taking='6'))
    result = await trader.place_order(
        TradeSide.SELL, TICKER, Decimal('0.59'), Decimal('10')
    )
    assert result.order.status == OrderStatus.FILLED
    assert result.order.average_price == Decimal('0.6')
    assert trader.position_manager.get_position(TICKER).quantity == Decimal('0')


async def test_fok_not_filled_rejects_without_halting(fake_mm) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond('predict place', err('PREDICT_ORDER_NOT_FILLED'))
    result = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )
    assert result.failure_reason == OrderFailureReason.UNKNOWN
    assert result.order.status == OrderStatus.REJECTED
    assert not trader.read_only


async def test_size_too_small_is_invalid(fake_mm) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond('predict place', err('PREDICT_ORDER_SIZE_TOO_SMALL'))
    result = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('1')
    )
    assert result.failure_reason == OrderFailureReason.INVALID_ORDER


async def test_insufficient_balance_maps_to_cash(fake_mm) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond('predict place', err('PREDICT_INSUFFICIENT_BALANCE'))
    result = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )
    assert result.failure_reason == OrderFailureReason.INSUFFICIENT_CASH


@pytest.mark.parametrize(
    'code', ['AWAITING_MFA', 'PREDICT_GEOBLOCKED', 'PREDICT_AUTH_REQUIRED']
)
async def test_halting_errors_stop_further_orders(fake_mm, code) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond('predict place', err(code))
    first = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )
    assert first.failure_reason == OrderFailureReason.TRADING_DISABLED
    assert trader.read_only and code in trader.halt_reason

    second = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )
    assert second.failure_reason == OrderFailureReason.TRADING_DISABLED
    assert len(fake_mm.calls('predict place')) == 1

    trader.resume()
    assert not trader.read_only


async def test_delayed_status_halts(fake_mm) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond('predict place', place_reply(status='delayed'))
    result = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )
    assert result.order.status == OrderStatus.UNKNOWN
    assert result.failure_reason == OrderFailureReason.UNKNOWN
    assert trader.read_only


async def test_unmatched_response_is_rejected(fake_mm) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond('predict place', place_reply(status='unmatched', success=False))
    result = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.56'), Decimal('10')
    )
    assert result.order.status == OrderStatus.REJECTED
    assert not trader.read_only


async def test_pre_trade_checks_run_before_the_wallet(fake_mm) -> None:
    trader = make_trader(fake_mm, max_order_notional=Decimal('25'))
    await trader.preflight()
    fake_mm.respond('predict place', place_reply())

    too_big = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.5'), Decimal('100')
    )
    assert too_big.failure_reason == OrderFailureReason.RISK_CHECK_FAILED
    bad_price = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('1.2'), Decimal('1')
    )
    assert bad_price.failure_reason == OrderFailureReason.INVALID_ORDER
    no_position = await trader.place_order(
        TradeSide.SELL, TICKER, Decimal('0.5'), Decimal('1')
    )
    assert no_position.failure_reason == OrderFailureReason.INVALID_ORDER
    not_poly = await trader.place_order(
        TradeSide.BUY, CashTicker.POLYMARKET_USDC, Decimal('0.5'), Decimal('1')
    )
    assert not_poly.failure_reason == OrderFailureReason.INVALID_ORDER
    first = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.5'), Decimal('1'), 'id-1'
    )
    dup = await trader.place_order(
        TradeSide.BUY, TICKER, Decimal('0.5'), Decimal('1'), 'id-1'
    )
    assert first.failure_reason is None
    assert dup.failure_reason == OrderFailureReason.DUPLICATE_ORDER
    assert len(fake_mm.calls('predict place')) == 1


async def test_sync_positions_loads_tokens(fake_mm) -> None:
    trader = make_trader(fake_mm)
    await trader.preflight()
    fake_mm.respond(
        'predict positions',
        ok(
            'positions',
            {
                'positions': [
                    {'asset': 'T1', 'size': 4, 'avgPrice': 0.5, 'title': 'Fed cut?'},
                    {'asset': 'T9', 'size': 0},
                ]
            },
        ),
    )
    loaded = await trader.sync_positions()
    assert [p.ticker.symbol for p in loaded] == ['T1']
    assert trader.position_manager.get_position(TICKER).quantity == Decimal('4')
