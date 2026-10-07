"""An oracle3 ``Trader`` that executes Polymarket orders through MetaMask Agent Wallet.

It is a drop-in replacement for ``oracle3.trader.polymarket_trader.PolymarketTrader``:
strategies call ``place_order`` exactly as before, but no private key is ever
handed to oracle3. Custody, signing, simulation, threat scanning and Guard
Mode policy stay in Agent Wallet, and oracle3 keeps its own pre-trade checks
(kill switch, idempotency keys, cash, risk limits) in front of them.

Defaults are deliberately conservative:

* testnet unless the caller passes ``allow_mainnet=True``;
* fill-or-kill orders only, so a position is never left half-built;
* a per-order notional cap (``max_order_notional``, $25 by default);
* any approval prompt, policy block, geoblock or order whose status cannot be
  read switches the trader to read-only, so nothing else is sent until a
  person has looked.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from oracle3.position.position_manager import Position, PositionManager
from oracle3.ticker.ticker import CashTicker, PolyMarketTicker, Ticker
from oracle3.trader.trader import Trader
from oracle3.trader.types import (
    Order,
    OrderFailureReason,
    OrderStatus,
    PlaceOrderResult,
    Trade,
    TradeSide,
)

from oracle3_extras.metamask.client import AgentWalletClient, PlaceResult
from oracle3_extras.metamask.errors import (
    AgentWalletError,
    ApprovalRequiredError,
    CLITimeoutError,
    GeoblockedError,
    InsufficientBalanceError,
    MainnetNotAllowedError,
    OrderRejectedError,
    PredictSetupError,
    SessionError,
)

if TYPE_CHECKING:
    from oracle3.alerts.alerter import Alerter
    from oracle3.data.market_data_manager import MarketDataManager
    from oracle3.risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)

# Errors after which nothing else should be sent until a person intervenes.
_HALTING_ERRORS = (
    ApprovalRequiredError,
    GeoblockedError,
    SessionError,
    PredictSetupError,
    CLITimeoutError,
)


class AgentWalletTrader(Trader):
    """Polymarket execution via ``mm predict``.

    Args:
        market_data: oracle3 market data manager.
        risk_manager: oracle3 risk manager; ``check_trade`` runs before every order.
        position_manager: oracle3 position manager, kept in sync with fills.
        client: Agent Wallet CLI client. Defaults to ``AgentWalletClient()``.
        allow_mainnet: Required to trade when predict mode is ``mainnet``.
        max_order_notional: Largest ``quantity * limit_price`` per order, in dollars.
        alerter: Optional oracle3 alerter.
    """

    def __init__(
        self,
        market_data: MarketDataManager,
        risk_manager: RiskManager,
        position_manager: PositionManager,
        client: AgentWalletClient | None = None,
        *,
        allow_mainnet: bool = False,
        max_order_notional: Decimal = Decimal('25'),
        alerter: Alerter | None = None,
    ) -> None:
        super().__init__(market_data, risk_manager, position_manager, alerter=alerter)
        self.client = client or AgentWalletClient()
        self.allow_mainnet = allow_mainnet
        self.max_order_notional = max_order_notional
        self.mode: str | None = None
        self.halt_reason: str | None = None

    # -- lifecycle ----------------------------------------------------------

    async def preflight(self) -> dict[str, Any]:
        """Check mode and geoblock, then load the pUSD balance.

        Raises:
            MainnetNotAllowedError: mode is mainnet and ``allow_mainnet`` is False.
            GeoblockedError: mode is mainnet and Polymarket blocks this IP.
        """
        mode = await asyncio.to_thread(self.client.predict_mode)
        geoblock = None
        if mode == 'mainnet':
            if not self.allow_mainnet:
                raise MainnetNotAllowedError(
                    'Predict mode is mainnet. Pass allow_mainnet=True to trade real '
                    'funds, or switch with `mm predict mode testnet`.',
                    code='MAINNET_NOT_ALLOWED',
                )
            geoblock = await asyncio.to_thread(self.client.geoblock)
            if geoblock.blocked:
                raise GeoblockedError(
                    'Polymarket is unavailable from this IP address '
                    f'({geoblock.country or "unknown country"}).',
                    code='PREDICT_GEOBLOCKED',
                )
        self.mode = mode
        balance = await self.sync_balance()
        return {
            'mode': mode,
            'balance': str(balance),
            'geoblocked': None if geoblock is None else geoblock.blocked,
            'attribution': self.client.attribution.enabled,
        }

    async def sync_balance(self) -> Decimal:
        """Replace the local pUSD position with the deposit-wallet balance."""
        balance = await asyncio.to_thread(self.client.balance)
        existing = self.position_manager.get_position(CashTicker.POLYMARKET_USDC)
        self.position_manager.update_position(
            Position(
                ticker=CashTicker.POLYMARKET_USDC,
                quantity=balance,
                average_cost=Decimal('1'),
                realized_pnl=existing.realized_pnl if existing else Decimal('0'),
            )
        )
        return balance

    async def sync_positions(self) -> list[Position]:
        """Load open outcome-token positions from the deposit wallet."""
        positions = await asyncio.to_thread(self.client.positions)
        loaded: list[Position] = []
        for item in positions:
            if item.size <= 0:
                continue
            ticker = PolyMarketTicker.from_token_id(item.token_id, name=item.title)
            existing = self.position_manager.get_position(ticker)
            position = Position(
                ticker=ticker,
                quantity=item.size,
                average_cost=item.avg_price,
                realized_pnl=existing.realized_pnl if existing else Decimal('0'),
            )
            self.position_manager.update_position(position)
            loaded.append(position)
        return loaded

    def halt(self, reason: str) -> None:
        """Stop sending orders until :meth:`resume` is called."""
        self.halt_reason = reason
        self.set_read_only(True)
        logger.error('Agent Wallet trading halted: %s', reason)

    def resume(self) -> None:
        self.halt_reason = None
        self.set_read_only(False)

    # -- orders -------------------------------------------------------------

    async def _reject(
        self, reason: OrderFailureReason, ticker: Ticker, order: Order | None = None
    ) -> PlaceOrderResult:
        if self.alerter:
            try:
                await self.alerter.on_order_rejected(reason, ticker)
            except Exception:
                logger.debug('alerter.on_order_rejected() failed', exc_info=True)
        return PlaceOrderResult(order=order, failure_reason=reason)

    @staticmethod
    def _empty_order(
        status: OrderStatus,
        side: TradeSide,
        ticker: Ticker,
        limit_price: Decimal,
        quantity: Decimal,
    ) -> Order:
        return Order(
            status=status,
            side=side,
            ticker=ticker,
            limit_price=limit_price,
            filled_quantity=Decimal('0'),
            average_price=Decimal('0'),
            trades=[],
            remaining=quantity,
            commission=Decimal('0'),
        )

    def _order_from_result(
        self,
        result: PlaceResult,
        side: TradeSide,
        ticker: Ticker,
        limit_price: Decimal,
        quantity: Decimal,
    ) -> Order:
        """Turn Polymarket's making/taking amounts into an oracle3 order.

        A buy gives pUSD (making) and receives shares (taking); a sell gives
        shares and receives pUSD. Fees are already inside these amounts, so
        the average price is the all-in price and commission is zero.
        """
        if result.status in {'delayed', 'live'} or (
            result.success and not result.status
        ):
            return self._empty_order(
                OrderStatus.UNKNOWN, side, ticker, limit_price, quantity
            )
        if not result.matched:
            return self._empty_order(
                OrderStatus.REJECTED, side, ticker, limit_price, quantity
            )

        if side == TradeSide.BUY:
            shares, dollars = result.taking_amount, result.making_amount
        else:
            shares, dollars = result.making_amount, result.taking_amount
        if shares > quantity * 1000:  # amounts came back in 6-decimal base units
            shares, dollars = shares / Decimal(1_000_000), dollars / Decimal(1_000_000)
        if shares <= 0:
            return self._empty_order(
                OrderStatus.UNKNOWN, side, ticker, limit_price, quantity
            )

        average = dollars / shares
        trade = Trade(
            side=side,
            ticker=ticker,
            price=average,
            quantity=shares,
            commission=Decimal('0'),
        )
        return Order(
            status=OrderStatus.FILLED,
            side=side,
            ticker=ticker,
            limit_price=limit_price,
            filled_quantity=shares,
            average_price=average,
            trades=[trade],
            remaining=max(quantity - shares, Decimal('0')),
            commission=Decimal('0'),
        )

    async def place_order(  # noqa: C901
        self,
        side: TradeSide,
        ticker: Ticker,
        limit_price: Decimal,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> PlaceOrderResult:
        guard_failure = self._check_order_guard(client_order_id)
        if guard_failure is not None:
            return await self._reject(guard_failure, ticker)
        if self.mode is None:
            logger.warning('place_order called before preflight(); rejecting')
            return await self._reject(OrderFailureReason.TRADING_DISABLED, ticker)
        if self.mode == 'mainnet' and not self.allow_mainnet:
            return await self._reject(OrderFailureReason.TRADING_DISABLED, ticker)

        if quantity <= 0 or not (Decimal('0') < limit_price < Decimal('1')):
            return await self._reject(OrderFailureReason.INVALID_ORDER, ticker)
        if not isinstance(ticker, PolyMarketTicker) or not ticker.token_id:
            return await self._reject(OrderFailureReason.INVALID_ORDER, ticker)
        if not self.is_ticker_tradable(ticker):
            return await self._reject(OrderFailureReason.MARKET_NOT_ALLOWED, ticker)
        if quantity * limit_price > self.max_order_notional:
            return await self._reject(OrderFailureReason.RISK_CHECK_FAILED, ticker)

        if side == TradeSide.SELL:
            position = self.position_manager.get_position(ticker)
            if position is None or position.quantity < quantity:
                return await self._reject(OrderFailureReason.INVALID_ORDER, ticker)
        else:
            cash = self.position_manager.get_position(ticker.collateral)
            if cash is None or cash.quantity < quantity * limit_price:
                return await self._reject(OrderFailureReason.INSUFFICIENT_CASH, ticker)

        if not await self.risk_manager.check_trade(ticker, side, quantity, limit_price):
            return await self._reject(OrderFailureReason.RISK_CHECK_FAILED, ticker)

        try:
            result = await asyncio.to_thread(
                self.client.place,
                ticker.token_id,
                side.value,
                quantity,
                limit_price,
                order_type='FOK',
            )
        except InsufficientBalanceError:
            return await self._reject(OrderFailureReason.INSUFFICIENT_CASH, ticker)
        except OrderRejectedError as exc:
            order = self._empty_order(
                OrderStatus.REJECTED, side, ticker, limit_price, quantity
            )
            self.orders.append(order)
            reason = (
                OrderFailureReason.INVALID_ORDER
                if exc.code == 'PREDICT_ORDER_SIZE_TOO_SMALL'
                else OrderFailureReason.UNKNOWN
            )
            return await self._reject(reason, ticker, order)
        except _HALTING_ERRORS as exc:
            self.halt(f'{exc.code}: {exc}')
            return await self._reject(OrderFailureReason.TRADING_DISABLED, ticker)
        except AgentWalletError as exc:
            logger.error('Agent Wallet order failed: %s', exc)
            return await self._reject(OrderFailureReason.UNKNOWN, ticker)

        order = self._order_from_result(result, side, ticker, limit_price, quantity)
        self.orders.append(order)
        if order.status == OrderStatus.UNKNOWN:
            self.halt(
                f'order {result.order_id or "?"} returned status '
                f'{result.status or "empty"}; check `mm predict orders`'
            )
            return await self._reject(OrderFailureReason.UNKNOWN, ticker, order)
        if order.status != OrderStatus.FILLED:
            return await self._reject(OrderFailureReason.UNKNOWN, ticker, order)

        for trade in order.trades:
            self.position_manager.apply_trade(trade)
        try:
            await self.sync_balance()
        except AgentWalletError:
            logger.debug('balance sync after fill failed', exc_info=True)
        return PlaceOrderResult(order=order)
