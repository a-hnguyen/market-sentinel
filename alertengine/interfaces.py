"""The swappable seams: screening, market data, rules, and notification.

These abstract boundaries are the whole point of the architecture: they let the
data source, alert rule, and notifier be replaced later (web dashboard, IBKR,
etc.) without touching the engine.
"""

from abc import ABC, abstractmethod
from datetime import datetime
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


class HistoricalBarFeed(ABC):
    """Read-only, range-based market-data source.

    Unlike ``DataFeed``, this seam does not imply a websocket. It supports
    polling providers such as Robinhood MCP while keeping transport and OAuth
    details outside strategy code.
    """

    @abstractmethod
    async def fetch_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> list[Bar]:
        """Return provider-native 1-minute bars in chronological order."""


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


class ArmedTriggerRule(ABC):
    """Optional rule that replaces candle confirmation after a setup arms."""

    @abstractmethod
    def evaluate(self, symbol: str, bars: list[Bar]) -> dict[str, float] | None:
        """Return alert context when the armed trigger passes, otherwise None."""

    def evaluate_since(
        self, symbol: str, bars: list[Bar], armed_at: datetime
    ) -> dict[str, float] | None:
        """Evaluate an armed window, defaulting to the latest-bar behavior.

        Window-aware rules may override this to combine observations made on
        separate bars without owning mutable state. The engine remains the
        source of truth for when the window began and when it expires.
        """
        return self.evaluate(symbol, bars)

    def evaluate_armed(
        self,
        symbol: str,
        bars: list[Bar],
        armed_at: datetime,
        setup_context: dict,
    ) -> dict[str, float] | None:
        """Evaluate with the captured setup values; legacy rules use the timestamp."""
        return self.evaluate_since(symbol, bars, armed_at)


class Notifier(ABC):
    @abstractmethod
    async def send(self, alert: Alert) -> None:
        """Deliver an alert."""
