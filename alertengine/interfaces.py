"""The four swappable seams: Screener, DataFeed, AlertRule, Notifier.

These abstract boundaries are the whole point of the architecture: they let the
data source, alert rule, and notifier be replaced later (web dashboard, IBKR,
etc.) without touching the engine.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator

from .models import Alert, Bar, Candidate


class Screener(ABC):
    @abstractmethod
    async def get_candidates(self) -> list[Candidate]:
        """Run the screen once, return candidates with criteria data."""


class DataFeed(ABC):
    @abstractmethod
    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        """Yield 1-min bars for the given symbols as they arrive."""


class AlertRule(ABC):
    @abstractmethod
    def evaluate(self, symbol: str, bars: list[Bar]) -> Alert | None:
        """Given the symbol's recent 2-min bar history, return an Alert if the
        setup fires this bar, else None. Stateless: the engine owns history."""


class ConfirmationRule(ABC):
    """Optional strategy-specific gate applied after bar-pattern confirmation."""

    @abstractmethod
    def evaluate(self, symbol: str, bars: list[Bar]) -> dict[str, float] | None:
        """Return alert context when confirmation passes, otherwise None."""


class Notifier(ABC):
    @abstractmethod
    async def send(self, alert: Alert) -> None:
        """Deliver an alert."""
