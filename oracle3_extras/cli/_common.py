"""Helpers shared by the command groups: JSON output and error reporting."""

from __future__ import annotations

import json
from typing import Any, NoReturn

import click


def echo_json(payload: Any) -> None:
    """Print one JSON document on stdout; progress messages go to stderr."""
    click.echo(json.dumps(payload, indent=2, default=str))


def fail(code: str, message: str, hint: str = '') -> NoReturn:
    echo_json({'ok': False, 'error': {'code': code, 'message': message, 'hint': hint}})
    raise SystemExit(1)


def run(coro: Any) -> Any:
    """Run a coroutine, turning network failures into a JSON error."""
    import asyncio

    from oracle3_extras._http import APIError

    try:
        return asyncio.run(coro)
    except APIError as exc:
        fail(
            'API_ERROR',
            str(exc),
            'Retry later; the service may be rate limiting or temporarily down.',
        )


def scan_options(command: Any) -> Any:
    """Options shared by every command that scans relations."""
    options = [
        click.option(
            '--contracts',
            type=float,
            default=1.0,
            show_default=True,
            help='Size of the top-of-book check on every leg.',
        ),
        click.option(
            '--maker',
            is_flag=True,
            help='Price fees as resting orders instead of taker.',
        ),
        click.option(
            '--max-contracts',
            type=float,
            default=None,
            help='Cap for the order-book walk.',
        ),
        click.option(
            '--min-edge',
            type=float,
            default=0.0,
            show_default=True,
            help='Smallest net edge per contract, in dollars.',
        ),
        click.option(
            '--top',
            type=int,
            default=20,
            show_default=True,
            help='Opportunities to print.',
        ),
        click.option(
            '--no-depth',
            is_flag=True,
            help='Skip the order-book check (top of book only).',
        ),
    ]
    for option in reversed(options):
        command = option(command)
    return command
