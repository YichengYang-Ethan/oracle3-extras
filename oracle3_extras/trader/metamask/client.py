"""A typed Python wrapper around the MetaMask Agent Wallet CLI (``mm``).

Every call runs ``mm <command> ... --json`` as a subprocess and reads the
CLI's result envelope:

* success: ``{"ok": true, "data": {"command": ..., "params": ..., "result": ...}}``
* failure: ``{"ok": false, "error": {"code": ..., "message": ..., "hint": ...}}``

Keys, signing, transaction simulation, threat scanning and Guard Mode policy
all stay inside Agent Wallet. This module only forwards requests and parses
answers, so it never sees a private key, a mnemonic or a CLI token.

The ``mm`` CLI is a separate, source-available package from MetaMask and is
not bundled here. Install it with ``npm install -g @metamask/agent-wallet``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from oracle3_extras.trader.metamask.attribution import Attribution
from oracle3_extras.trader.metamask.errors import (
    CLINotInstalledError,
    CLIProtocolError,
    CLITimeoutError,
    error_from_envelope,
)

logger = logging.getLogger(__name__)

INSTALL_HINT = 'Install the CLI with: npm install -g @metamask/agent-wallet'
PREDICT_MODES = ('testnet', 'mainnet')
ORDER_TYPES = ('FOK', 'FAK', 'GTC', 'GTD')
SIDES = ('buy', 'sell')
# pUSD and outcome tokens use 6 decimals on Polygon.
_BASE_UNITS = Decimal(1_000_000)


def _dec(value: Any, default: str = '0') -> Decimal:
    if value is None or value == '':
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _fmt(value: Decimal | int | float | str) -> str:
    """Format a number for the CLI without scientific notation."""
    if isinstance(value, Decimal):
        return format(value, 'f')
    return str(value)


def _parse_envelope(text: str) -> dict[str, Any] | None:
    """Return the result envelope from ``mm`` output, or ``None``.

    Most commands print one JSON document. Streaming commands print NDJSON
    items followed by a ``_summary`` or ``_error`` line, so the last such line
    wins.
    """
    text = text.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and 'ok' in parsed:
        return parsed
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if 'ok' in item:
            return item
        if '_error' in item:
            return {'ok': False, 'error': item['_error']}
        if '_summary' in item:
            return {'ok': True, 'data': item['_summary']}
    return None


@dataclass(frozen=True)
class GeoblockStatus:
    blocked: bool
    country: str = ''
    region: str = ''


@dataclass(frozen=True)
class Quote:
    """Executable cost of a size against the current book (``mm predict quote``)."""

    side: str
    requested_size: Decimal
    filled_size: Decimal
    average_price: Decimal | None
    worst_price: Decimal | None
    cost: Decimal
    proceeds: Decimal
    fully_filled: bool


@dataclass(frozen=True)
class PlaceResult:
    """Polymarket's answer to one order (``mm predict place``)."""

    order_id: str
    status: str
    success: bool
    making_amount: Decimal
    taking_amount: Decimal
    error_msg: str = ''
    transaction_hashes: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def matched(self) -> bool:
        return self.success and self.status == 'matched'


@dataclass(frozen=True)
class PredictPosition:
    token_id: str
    size: Decimal
    avg_price: Decimal
    cur_price: Decimal | None = None
    condition_id: str = ''
    outcome: str = ''
    title: str = ''
    redeemable: bool = False


@dataclass
class AgentWalletClient:
    """Run ``mm`` commands and return parsed results.

    Args:
        executable: Name or path of the ``mm`` binary.
        timeout: Seconds before a call is abandoned. Order placement can wait
            on wallet jobs, so the default is generous.
        env: Extra environment variables for every call.
        attribution: Integration attribution; see
            :mod:`oracle3_extras.trader.metamask.attribution`.
    """

    executable: str = 'mm'
    timeout: float = 120.0
    env: Mapping[str, str] | None = None
    attribution: Attribution = field(default_factory=Attribution.from_env)
    _flag_cache: dict[str, bool] = field(default_factory=dict, init=False, repr=False)

    # -- plumbing -----------------------------------------------------------

    def _resolve(self) -> str:
        path = (
            self.executable
            if os.sep in self.executable
            else shutil.which(self.executable)
        )
        if not path or not os.path.exists(path):
            raise CLINotInstalledError(
                f'MetaMask Agent Wallet CLI not found: {self.executable!r}',
                code='CLI_NOT_INSTALLED',
                hint=INSTALL_HINT,
            )
        return path

    def _env(self) -> dict[str, str]:
        merged = dict(os.environ)
        merged.update(self.attribution.env())
        if self.env:
            merged.update(self.env)
        return merged

    def run(self, args: Sequence[str], *, timeout: float | None = None) -> Any:
        """Run ``mm <args> --json`` and return the envelope's ``data``."""
        cmd = [self._resolve(), *args, '--json']
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env=self._env(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CLITimeoutError(
                f'mm {" ".join(args)} timed out after {exc.timeout}s',
                code='CLI_TIMEOUT',
                hint='The order may still be pending; check `mm predict orders`.',
            ) from exc
        envelope = _parse_envelope(proc.stdout) or _parse_envelope(proc.stderr)
        if envelope is None:
            tail = (proc.stderr or proc.stdout or '').strip()[-500:]
            raise CLIProtocolError(
                f'mm {" ".join(args)} exited {proc.returncode} without a JSON '
                f'result: {tail}',
                code='CLI_PROTOCOL',
            )
        if envelope.get('ok') is True:
            return envelope.get('data')
        raise error_from_envelope(envelope.get('error'))

    def _predict(self, *args: str, timeout: float | None = None) -> dict[str, Any]:
        data = self.run(['predict', *args], timeout=timeout)
        if isinstance(data, dict) and 'result' in data and 'command' in data:
            result = data['result']
            return result if isinstance(result, dict) else {'value': result}
        return data if isinstance(data, dict) else {'value': data}

    def supports_flag(self, command: Sequence[str], flag: str) -> bool:
        """Return whether ``mm <command> --help`` lists ``flag``. Cached."""
        key = ' '.join([*command, flag])
        if key not in self._flag_cache:
            try:
                proc = subprocess.run(
                    [self._resolve(), *command, '--help'],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=self._env(),
                    check=False,
                )
                self._flag_cache[key] = flag in (proc.stdout + proc.stderr)
            except (OSError, subprocess.SubprocessError, CLINotInstalledError):
                self._flag_cache[key] = False
        return self._flag_cache[key]

    # -- session ------------------------------------------------------------

    def doctor(self) -> dict[str, Any]:
        """``mm doctor``: CLI version, skills, authentication and init state."""
        data = self.run(['doctor'])
        return data if isinstance(data, dict) else {'value': data}

    def predict_mode(self) -> str:
        """Current predict mode, ``testnet`` or ``mainnet``."""
        result = self._predict('mode')
        return str(result.get('mode', '')).lower()

    def set_predict_mode(self, mode: str) -> str:
        if mode not in PREDICT_MODES:
            raise ValueError(f'mode must be one of {PREDICT_MODES}')
        result = self._predict('mode', mode)
        return str(result.get('mode', mode)).lower()

    def geoblock(self) -> GeoblockStatus:
        """``mm predict geoblock``: whether Polymarket serves this IP."""
        result = self._predict('geoblock')
        return GeoblockStatus(
            blocked=bool(result.get('blocked')),
            country=str(result.get('country') or ''),
            region=str(result.get('region') or ''),
        )

    def balance(self, *, sync: bool = True) -> Decimal:
        """pUSD collateral in the predict deposit wallet, in dollars."""
        args = ['balance', '--sync'] if sync else ['balance']
        result = self._predict(*args)
        allowance = result.get('balanceAllowance') or {}
        raw = allowance.get('balance') if isinstance(allowance, dict) else None
        text = str(raw if raw is not None else result.get('balance', '0'))
        # The CLOB reports base units (6 decimals) as an integer string.
        if text.isdigit():
            return Decimal(text) / _BASE_UNITS
        return _dec(text)

    # -- trading ------------------------------------------------------------

    def quote(
        self,
        token_id: str,
        side: str,
        size: Decimal,
        *,
        limit_price: Decimal | None = None,
        tick_size: str | None = None,
    ) -> Quote:
        """Walk the live book for ``size`` shares without placing anything."""
        side = side.lower()
        if side not in SIDES:
            raise ValueError(f'side must be one of {SIDES}')
        args = ['quote', '--token-id', token_id, '--side', side, '--size', _fmt(size)]
        if limit_price is not None:
            args += ['--limit-price', _fmt(limit_price)]
        if tick_size:
            args += ['--tick-size', tick_size]
        q = self._predict(*args).get('quote') or {}
        avg = q.get('averagePrice')
        worst = q.get('worstPrice')
        requested = _dec(q.get('requestedSize'), _fmt(size))
        filled = _dec(q.get('filledSize'))
        return Quote(
            side=str(q.get('side', side)).lower(),
            requested_size=requested,
            filled_size=filled,
            average_price=None if avg is None else _dec(avg),
            worst_price=None if worst is None else _dec(worst),
            cost=_dec(q.get('cost')),
            proceeds=_dec(q.get('proceeds')),
            fully_filled=bool(q.get('fullyFilled', filled >= requested)),
        )

    def place(
        self,
        token_id: str,
        side: str,
        size: Decimal,
        price: Decimal,
        *,
        order_type: str = 'FOK',
        tick_size: str | None = None,
        post_only: bool = False,
        expiration: int | None = None,
    ) -> PlaceResult:
        """Place one Polymarket order through Agent Wallet.

        ``price`` is the worst acceptable price per share, between 0 and 1.
        The default ``FOK`` either fills the whole size at or better than
        ``price`` or does nothing.
        """
        side = side.lower()
        if side not in SIDES:
            raise ValueError(f'side must be one of {SIDES}')
        order_type = order_type.upper()
        if order_type not in ORDER_TYPES:
            raise ValueError(f'order_type must be one of {ORDER_TYPES}')
        args = [
            'place',
            '--token-id',
            token_id,
            '--side',
            side,
            '--size',
            _fmt(size),
            '--price',
            _fmt(price),
            '--order-type',
            order_type,
        ]
        if tick_size:
            args += ['--tick-size', tick_size]
        if post_only:
            args.append('--post-only')
        if expiration is not None:
            args += ['--expiration', str(int(expiration))]
        metadata = self.attribution.metadata
        if metadata and self.supports_flag(['predict', 'place'], '--metadata'):
            args += ['--metadata', metadata]
        result = self._predict(*args)
        response = result.get('response') or {}
        return PlaceResult(
            order_id=str(response.get('orderId') or response.get('orderID') or ''),
            status=str(response.get('status') or '').lower(),
            success=bool(response.get('success')),
            making_amount=_dec(response.get('makingAmount')),
            taking_amount=_dec(response.get('takingAmount')),
            error_msg=str(response.get('errorMsg') or ''),
            transaction_hashes=tuple(response.get('transactionHashes') or ()),
            raw=result,
        )

    def cancel(
        self, order_id: str | None = None, *, all_orders: bool = False
    ) -> dict[str, Any]:
        if not order_id and not all_orders:
            raise ValueError('pass an order_id or all_orders=True')
        args = (
            ['cancel', '--all']
            if all_orders
            else ['cancel', '--order-id', order_id or '']
        )
        return self._predict(*args)

    def orders(self) -> list[dict[str, Any]]:
        result = self._predict('orders')
        orders = result.get('orders', result.get('value', []))
        return list(orders) if isinstance(orders, list) else []

    def positions(self) -> list[PredictPosition]:
        result = self._predict('positions')
        positions: list[PredictPosition] = []
        for item in result.get('positions') or []:
            if not isinstance(item, dict):
                continue
            token_id = str(item.get('asset') or item.get('tokenId') or '')
            if not token_id:
                continue
            cur = item.get('curPrice')
            positions.append(
                PredictPosition(
                    token_id=token_id,
                    size=_dec(item.get('size')),
                    avg_price=_dec(item.get('avgPrice')),
                    cur_price=None if cur is None else _dec(cur),
                    condition_id=str(item.get('conditionId') or ''),
                    outcome=str(item.get('outcome') or ''),
                    title=str(item.get('title') or ''),
                    redeemable=bool(item.get('redeemable')),
                )
            )
        return positions

    def redeem(
        self, condition_id: str | None = None, *, all_positions: bool = False
    ) -> dict[str, Any]:
        """Redeem winning positions back to pUSD and wait for confirmation."""
        if all_positions:
            return self._predict('redeem', '--all', '--wait')
        if not condition_id:
            raise ValueError('pass a condition_id or all_positions=True')
        return self._predict('redeem', condition_id, '--wait')

    def redeemable(self) -> dict[str, Any]:
        """``mm predict redeem list``."""
        return self._predict('redeem', 'list')
