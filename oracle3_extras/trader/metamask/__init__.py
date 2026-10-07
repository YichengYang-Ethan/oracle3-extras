"""oracle3 integration for MetaMask Agent Wallet.

Routes Polymarket orders from oracle3 strategies and agents through MetaMask
Agent Wallet, so oracle3 never holds a private key and every order passes
Agent Wallet's simulation, threat scan and Guard Mode policy.

Requires the ``mm`` CLI (``npm install -g @metamask/agent-wallet``), a signed-in
session (``mm login``), an initialized wallet (``mm init``) and predict setup
(``mm predict setup --wait``).
"""

from oracle3_extras.trader.metamask.attribution import (
    ATTRIBUTION_ENV,
    INTEGRATION_ID,
    Attribution,
    metadata_bytes32,
)
from oracle3_extras.trader.metamask.client import (
    AgentWalletClient,
    GeoblockStatus,
    PlaceResult,
    PredictPosition,
    Quote,
)
from oracle3_extras.trader.metamask.errors import (
    AgentWalletError,
    ApprovalRequiredError,
    CLINotInstalledError,
    CLIProtocolError,
    CLITimeoutError,
    GeoblockedError,
    InsufficientBalanceError,
    MainnetNotAllowedError,
    OrderRejectedError,
    PredictSetupError,
    SessionError,
)
from oracle3_extras.trader.metamask.trader import AgentWalletTrader

__all__ = [
    'ATTRIBUTION_ENV',
    'INTEGRATION_ID',
    'AgentWalletClient',
    'AgentWalletError',
    'AgentWalletTrader',
    'ApprovalRequiredError',
    'Attribution',
    'CLINotInstalledError',
    'CLIProtocolError',
    'CLITimeoutError',
    'GeoblockStatus',
    'GeoblockedError',
    'InsufficientBalanceError',
    'MainnetNotAllowedError',
    'OrderRejectedError',
    'PlaceResult',
    'PredictPosition',
    'PredictSetupError',
    'Quote',
    'SessionError',
    'metadata_bytes32',
]
