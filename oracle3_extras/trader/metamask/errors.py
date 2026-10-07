"""Errors raised by the MetaMask Agent Wallet integration.

The ``mm`` CLI reports failures as ``{"ok": false, "error": {"code", "message",
"hint"}}`` with stable codes (see the Agent Wallet error-code reference).
:func:`error_from_envelope` maps those codes onto a small hierarchy so the
trader can tell "reject this one order" apart from "stop trading until a human
looks".
"""

from __future__ import annotations

from typing import Any


class AgentWalletError(Exception):
    """Base class. ``code`` is the CLI's stable error code when one exists."""

    def __init__(self, message: str, *, code: str = 'UNKNOWN', hint: str = '') -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint

    def __str__(self) -> str:
        text = f'{super().__str__()} [{self.code}]'
        return f'{text} Hint: {self.hint}' if self.hint else text


class CLINotInstalledError(AgentWalletError):
    """The ``mm`` executable was not found."""


class CLITimeoutError(AgentWalletError):
    """An ``mm`` call did not finish within the client's timeout."""


class CLIProtocolError(AgentWalletError):
    """``mm`` exited without a JSON envelope we could read."""


class SessionError(AgentWalletError):
    """Not signed in, not initialized, or the CLI token is no longer valid."""


class PredictSetupError(AgentWalletError):
    """Predict setup, credentials or token allowances are missing."""


class GeoblockedError(AgentWalletError):
    """Polymarket is unavailable from the current IP address."""


class MainnetNotAllowedError(AgentWalletError):
    """Predict mode is mainnet but the caller did not opt in to real funds."""


class InsufficientBalanceError(AgentWalletError):
    """The predict deposit wallet does not hold enough pUSD."""


class OrderRejectedError(AgentWalletError):
    """The order was valid but not executed (FOK not filled, size too small)."""


class ApprovalRequiredError(AgentWalletError):
    """A wallet policy or 2FA approval stopped the transaction.

    The trader treats this as a stop signal: a person has to approve or change
    the policy in MetaMask before anything else is sent.
    """


_SESSION_CODES = frozenset(
    {
        'AUTH_FAILED',
        'AUTH_ERROR',
        'TOKEN_INVALID',
        'TOKEN_REFRESH_FAILED',
        'NOT_AUTHENTICATED',
        'NOT_INITIALIZED',
    }
)
_SETUP_CODES = frozenset(
    {
        'PREDICT_SETUP_REQUIRED',
        'PREDICT_AUTH_REQUIRED',
        'PREDICT_AUTH_INVALID',
        'PREDICT_INSUFFICIENT_ALLOWANCE',
    }
)
_GEOBLOCK_CODES = frozenset(
    {'PREDICT_GEOBLOCKED', 'PREDICT_UNAVAILABLE_FOR_LEGAL_REASONS'}
)
_BALANCE_CODES = frozenset({'PREDICT_INSUFFICIENT_BALANCE', 'INSUFFICIENT_FUNDS'})
_REJECTED_CODES = frozenset(
    {'PREDICT_ORDER_NOT_FILLED', 'PREDICT_ORDER_SIZE_TOO_SMALL'}
)
_APPROVAL_MARKERS = ('MFA', 'APPROVAL', 'DENIED', 'POLICY', 'OUTFLOW', 'ALLOWLIST')


def error_from_envelope(error: Any) -> AgentWalletError:
    """Build the most specific error for an ``mm`` error envelope."""
    if not isinstance(error, dict):
        return AgentWalletError(str(error) or 'mm reported an error')
    code = str(error.get('code') or 'UNKNOWN')
    message = str(error.get('message') or code)
    hint = str(error.get('hint') or '')

    if code in _SESSION_CODES:
        cls: type[AgentWalletError] = SessionError
    elif code in _SETUP_CODES:
        cls = PredictSetupError
    elif code in _GEOBLOCK_CODES:
        cls = GeoblockedError
    elif code in _BALANCE_CODES:
        cls = InsufficientBalanceError
    elif code in _REJECTED_CODES:
        cls = OrderRejectedError
    elif any(marker in code for marker in _APPROVAL_MARKERS):
        cls = ApprovalRequiredError
    else:
        cls = AgentWalletError
    return cls(message, code=code, hint=hint)
