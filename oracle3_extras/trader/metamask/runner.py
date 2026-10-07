"""Run an oracle3 strategy live with orders routed through MetaMask Agent Wallet.

Mirrors ``oracle3.live.live_trader.run_live_polymarket_trading``. The only
change is the trader: :class:`AgentWalletTrader` instead of a trader built
from a raw Polymarket private key.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from oracle3.data.market_data_manager import MarketDataManager
from oracle3.live.live_trader import run_live_trading
from oracle3.position.position_manager import PositionManager
from oracle3.risk.risk_manager import StandardRiskManager

from oracle3_extras.trader.metamask.client import AgentWalletClient
from oracle3_extras.trader.metamask.trader import AgentWalletTrader

if TYPE_CHECKING:
    from oracle3.alerts.alerter import Alerter
    from oracle3.data.live.live_data_source import LivePolyMarketDataSource
    from oracle3.storage.state_store import StateStore
    from oracle3.strategy.strategy import Strategy

logger = logging.getLogger(__name__)


async def run_live_agent_wallet_trading(
    data_source: LivePolyMarketDataSource,
    strategy: Strategy,
    client: AgentWalletClient | None = None,
    *,
    allow_mainnet: bool = False,
    max_order_notional: Decimal = Decimal('25'),
    max_position_size: Decimal = Decimal('100'),
    max_total_exposure: Decimal = Decimal('500'),
    duration: float | None = None,
    monitor: bool = False,
    state_store: StateStore | None = None,
    alerter: Alerter | None = None,
    emit_text: bool = True,
) -> dict[str, Any]:
    """Preflight the wallet, then run the strategy until ``duration`` elapses.

    Returns the preflight summary (mode, starting balance, attribution flag).
    """
    market_data = MarketDataManager()
    position_manager = PositionManager()
    risk_manager = StandardRiskManager(
        position_manager=position_manager,
        market_data=market_data,
        max_position_size=max_position_size,
        max_total_exposure=max_total_exposure,
        max_single_trade_size=max_order_notional,
        max_drawdown_pct=Decimal('0.2'),
    )
    trader = AgentWalletTrader(
        market_data=market_data,
        risk_manager=risk_manager,
        position_manager=position_manager,
        client=client,
        allow_mainnet=allow_mainnet,
        max_order_notional=max_order_notional,
        alerter=alerter,
    )
    summary = await trader.preflight()
    await trader.sync_positions()
    if emit_text:
        print(
            f'Agent Wallet ready: mode={summary["mode"]} '
            f'balance={summary["balance"]} pUSD'
        )

    await run_live_trading(
        data_source,
        strategy,
        trader,
        duration,
        state_store,
        alerter,
        True,
        None,
        monitor=monitor,
        exchange_name='Polymarket via MetaMask Agent Wallet',
        emit_text=emit_text,
    )
    return summary
