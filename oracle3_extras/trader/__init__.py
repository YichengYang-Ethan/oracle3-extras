"""Traders, mirroring ``oracle3.trader``.

- :mod:`oracle3_extras.trader.metamask`: execute Polymarket orders through
  MetaMask Agent Wallet, so oracle3 never holds a private key.
"""

from oracle3_extras.trader import metamask
from oracle3_extras.trader.metamask import AgentWalletClient, AgentWalletTrader

__all__ = ['AgentWalletClient', 'AgentWalletTrader', 'metamask']
