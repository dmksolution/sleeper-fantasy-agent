"""Sleeper fantasy football agent toolkit.

Read-only analysis and recommendation engine built on the public Sleeper API.
Nothing in this package can modify your Sleeper league: the API has no write
endpoints. The agent tells you what to do; you tap the button.
"""

from .client import SleeperClient, client
from .config import settings
from .league import League
from .store import init_db

__version__ = "1.0.0"

__all__ = ["SleeperClient", "client", "settings", "League", "init_db", "__version__"]
