"""``oracle3-extras metamask ...``: trade Polymarket through MetaMask Agent Wallet."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import click

from oracle3_extras.cli._common import echo_json as _echo
from oracle3_extras.trader.metamask.client import AgentWalletClient
from oracle3_extras.trader.metamask.errors import AgentWalletError


def _error(exc: AgentWalletError) -> dict[str, str]:
    message = str(exc.args[0]) if exc.args else exc.code
    return {'code': exc.code, 'message': message, 'hint': exc.hint}


def _fail(exc: AgentWalletError) -> None:
    _echo({'ok': False, 'error': _error(exc)})
    raise SystemExit(1)


@click.group()
@click.option(
    '--mm', 'executable', default='mm', show_default=True, help='Path to the mm CLI.'
)
@click.pass_context
def metamask(ctx: click.Context, executable: str) -> None:
    """Trade Polymarket through MetaMask Agent Wallet (the `mm` CLI)."""
    ctx.obj = AgentWalletClient(executable=executable)


@metamask.command()
@click.pass_obj
def doctor(client: AgentWalletClient) -> None:
    """Check the CLI, session, predict mode, geoblock and balance."""
    report: dict[str, Any] = {'ok': True, 'attribution': client.attribution.enabled}
    try:
        report['doctor'] = client.doctor()
        report['mode'] = client.predict_mode()
        geo = client.geoblock()
        report['geoblocked'] = geo.blocked
        report['balance_pusd'] = str(client.balance())
        report['metadata_flag_supported'] = client.supports_flag(
            ['predict', 'place'], '--metadata'
        )
    except AgentWalletError as exc:
        report.update(ok=False, error=_error(exc))
    _echo(report)
    if not report['ok']:
        raise SystemExit(1)


@metamask.command()
@click.argument('mode', required=False, type=click.Choice(['testnet', 'mainnet']))
@click.pass_obj
def mode(client: AgentWalletClient, mode: str | None) -> None:
    """Show the predict mode, or switch it."""
    try:
        current = client.set_predict_mode(mode) if mode else client.predict_mode()
    except AgentWalletError as exc:
        _fail(exc)
    _echo({'ok': True, 'mode': current})


@metamask.command()
@click.option('--token-id', required=True)
@click.option('--side', type=click.Choice(['buy', 'sell']), required=True)
@click.option('--size', type=str, required=True, help='Shares.')
@click.option('--limit-price', type=str, default=None)
@click.pass_obj
def quote(
    client: AgentWalletClient,
    token_id: str,
    side: str,
    size: str,
    limit_price: str | None,
) -> None:
    """Price a size against the live book without placing anything."""
    try:
        q = client.quote(
            token_id,
            side,
            Decimal(size),
            limit_price=Decimal(limit_price) if limit_price else None,
        )
    except AgentWalletError as exc:
        _fail(exc)
    _echo({'ok': True, 'quote': q.__dict__})


@metamask.command()
@click.option('--token-id', required=True)
@click.option('--side', type=click.Choice(['buy', 'sell']), required=True)
@click.option('--size', type=str, required=True, help='Shares.')
@click.option('--price', type=str, required=True, help='Worst acceptable price, 0-1.')
@click.option(
    '--order-type',
    type=click.Choice(['FOK', 'FAK', 'GTC']),
    default='FOK',
    show_default=True,
)
@click.option(
    '--allow-mainnet', is_flag=True, help='Required when predict mode is mainnet.'
)
@click.option('--yes', is_flag=True, help='Confirm that this places a real order.')
@click.pass_obj
def place(
    client: AgentWalletClient,
    token_id: str,
    side: str,
    size: str,
    price: str,
    order_type: str,
    allow_mainnet: bool,
    yes: bool,
) -> None:
    """Place one order through Agent Wallet."""
    if not yes:
        raise click.ClickException('Placing an order needs --yes.')
    try:
        if client.predict_mode() == 'mainnet' and not allow_mainnet:
            raise click.ClickException(
                'Predict mode is mainnet; pass --allow-mainnet to use real funds.'
            )
        result = client.place(
            token_id, side, Decimal(size), Decimal(price), order_type=order_type
        )
    except AgentWalletError as exc:
        _fail(exc)
    _echo(
        {
            'ok': True,
            'order_id': result.order_id,
            'status': result.status,
            'matched': result.matched,
            'making_amount': str(result.making_amount),
            'taking_amount': str(result.taking_amount),
            'transaction_hashes': list(result.transaction_hashes),
        }
    )


@metamask.command()
@click.pass_obj
def positions(client: AgentWalletClient) -> None:
    """List outcome-token positions in the predict deposit wallet."""
    try:
        items = client.positions()
    except AgentWalletError as exc:
        _fail(exc)
    _echo({'ok': True, 'positions': [p.__dict__ for p in items]})


@metamask.command()
@click.option('--condition-id', default=None)
@click.option(
    '--all', 'all_positions', is_flag=True, help='Redeem every winning position.'
)
@click.pass_obj
def redeem(
    client: AgentWalletClient, condition_id: str | None, all_positions: bool
) -> None:
    """Redeem resolved winning positions back to pUSD."""
    if not condition_id and not all_positions:
        raise click.ClickException('Pass --condition-id or --all.')
    try:
        result = client.redeem(condition_id, all_positions=all_positions)
    except AgentWalletError as exc:
        _fail(exc)
    _echo({'ok': True, 'result': result})


@metamask.command('run')
@click.option(
    '--strategy-ref', required=True, help='module:Class or /path/file.py:Class'
)
@click.option('--strategy-kwargs-json', default=None)
@click.option('--duration', type=float, default=None, help='Seconds to run.')
@click.option('--max-order-notional', type=str, default='25', show_default=True)
@click.option('--allow-mainnet', is_flag=True)
@click.option('--monitor', is_flag=True, help='Show the oracle3 TUI dashboard.')
@click.option(
    '--yes', is_flag=True, help='Confirm that the strategy may place real orders.'
)
@click.pass_obj
def run(
    client: AgentWalletClient,
    strategy_ref: str,
    strategy_kwargs_json: str | None,
    duration: float | None,
    max_order_notional: str,
    allow_mainnet: bool,
    monitor: bool,
    yes: bool,
) -> None:
    """Run an oracle3 strategy with orders routed through Agent Wallet."""
    if not yes:
        raise click.ClickException('Running a strategy that places orders needs --yes.')
    from oracle3.data.live.live_data_source import LivePolyMarketDataSource
    from oracle3.strategy.loader import load_strategy_class

    from oracle3_extras.trader.metamask.runner import run_live_agent_wallet_trading

    try:
        strategy_cls = load_strategy_class(strategy_ref)
        kwargs = json.loads(strategy_kwargs_json) if strategy_kwargs_json else {}
        strategy = strategy_cls(**kwargs)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise click.ClickException(f'Could not load strategy: {exc}') from exc

    data_source = LivePolyMarketDataSource(
        event_cache_file='events_cache.jsonl',
        polling_interval=60.0,
        orderbook_refresh_interval=10.0,
        reprocess_on_start=False,
    )
    try:
        summary = asyncio.run(
            run_live_agent_wallet_trading(
                data_source,
                strategy,
                client,
                allow_mainnet=allow_mainnet,
                max_order_notional=Decimal(max_order_notional),
                duration=duration,
                monitor=monitor,
            )
        )
    except AgentWalletError as exc:
        _fail(exc)
    _echo({'ok': True, 'session': summary})
