"""Synthetic 1-min bar feed for testing the whole pipeline with no API keys.

The default price path for each symbol is deliberately shaped to eventually
trigger the layer-1 setup: a flat warmup, a steady decline, then a sharp
capitulation that closes below the (trailing) lower Bollinger Band while RSI is
oversold. Pass your own `prices` to test other scenarios.
"""

import asyncio
from datetime import datetime, timedelta
from typing import AsyncIterator

from ..interfaces import DataFeed
from ..models import Bar


def _default_prices() -> list[float]:
    """A deterministic close path that fires the BB+RSI setup near the end."""
    prices: list[float] = []
    # Flat warmup around 50 (tiny alternation so std isn't exactly zero).
    for i in range(28):
        prices.append(50.0 + (0.15 if i % 2 == 0 else -0.15))
    # Steady decline 50 -> ~44.
    p = 50.0
    for _ in range(20):
        p -= 0.3
        prices.append(round(p, 2))
    # Sharp capitulation: fast drop below the trailing band, RSI deep oversold.
    for _ in range(8):
        p -= 1.0
        prices.append(round(p, 2))
    return prices


class MockFeed(DataFeed):
    def __init__(
        self,
        prices: dict[str, list[float]] | None = None,
        symbols: list[str] | None = None,
        interval: float = 0.0,
        start: datetime | None = None,
    ) -> None:
        """`prices` maps symbol -> close path. If omitted, each symbol in
        `symbols` gets the default firing path. `interval` is the async delay
        between successive minutes (0 for fast tests; small >0 for the REPL).
        """
        if prices is None:
            syms = symbols or ["MOCK"]
            prices = {s: _default_prices() for s in syms}
        self._prices = prices
        self._interval = interval
        self._start = start or datetime(2026, 1, 2, 9, 30)

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        paths = {s: self._prices[s] for s in symbols if s in self._prices}
        if not paths:
            return
        n = max(len(p) for p in paths.values())
        for i in range(n):
            for sym, path in paths.items():
                if i >= len(path):
                    continue
                close = path[i]
                prev = path[i - 1] if i > 0 else close
                high = max(prev, close) + 0.05
                low = min(prev, close) - 0.05
                yield Bar(
                    symbol=sym,
                    timestamp=self._start + timedelta(minutes=i),
                    open=prev,
                    high=high,
                    low=low,
                    close=close,
                    volume=1000.0,
                )
            if self._interval:
                await asyncio.sleep(self._interval)
