"""Bridge a range-based historical provider into the live ``DataFeed`` seam.

The existing engine consumes 1-minute bars and owns the clock-aligned 2-minute
aggregation. Robinhood MCP is query-based rather than a websocket, so this
adapter polls only completed 1-minute intervals, overlaps requests for recovery,
and emits each symbol/timestamp at most once per subscription.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from ..aggregator import bucket_start
from ..interfaces import DataFeed, HistoricalBarFeed
from ..models import Bar

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]

_LOG = logging.getLogger("alertengine.historical_poll")


def completed_minute(now: datetime) -> datetime:
    """Exclusive end bound for completed 1-minute bars."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return now.astimezone(timezone.utc).replace(second=0, microsecond=0)


class HistoricalPollingFeed(DataFeed):
    """Poll an async historical source and yield newly completed 1-minute bars."""

    def __init__(
        self,
        source: HistoricalBarFeed,
        *,
        poll_seconds: float = 15.0,
        overlap_minutes: int = 3,
        backfill_minutes: int = 180,
        clock: Clock | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if overlap_minutes < 0:
            raise ValueError("overlap_minutes must be non-negative")
        if backfill_minutes <= 0:
            raise ValueError("backfill_minutes must be positive")
        self._source = source
        self._poll_seconds = poll_seconds
        self._overlap = timedelta(minutes=overlap_minutes)
        self._backfill = timedelta(minutes=backfill_minutes)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep

    async def backfill_bars(self, symbols: list[str]) -> list[Bar]:
        """Warm the engine without evaluating historical signals."""
        end = completed_minute(self._clock())
        bars = await self._source.fetch_bars(symbols, end - self._backfill, end)
        _LOG.info(
            "event=historical_backfill symbols=%s bars=%d real=%d interpolated=%d",
            ",".join(symbols),
            len(bars),
            sum(not bar.interpolated for bar in bars),
            sum(bar.interpolated for bar in bars),
        )
        return bars

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        normalized = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
        if not normalized or any(not symbol for symbol in normalized):
            raise ValueError("symbols must not be empty")

        cursors: dict[str, datetime] | None = None
        while True:
            end = completed_minute(self._clock())
            if cursors is None:
                # The warm-up path drops its trailing partial 2-minute bucket.
                # Start this subscription at the current even-minute boundary
                # so the live aggregator can reconstruct that bucket cleanly.
                trailing_bucket = bucket_start(end - timedelta(minutes=1))
                first = trailing_bucket - timedelta(minutes=1)
                cursors = {symbol: first for symbol in normalized}

            start = min(cursors.values()) - self._overlap
            if start < end:
                bars = await self._source.fetch_bars(normalized, start, end)
                emitted = 0
                for bar in bars:
                    cursor = cursors.get(bar.symbol)
                    if cursor is None:
                        continue
                    if cursor < bar.timestamp < end:
                        cursors[bar.symbol] = bar.timestamp
                        emitted += 1
                        yield bar
                # A missing/unresolved symbol must not pin the global request
                # start forever. The configured overlap still recovers bars
                # that appear a little late on the next successful poll.
                completed_cursor = end - timedelta(minutes=1)
                for symbol in normalized:
                    cursors[symbol] = max(cursors[symbol], completed_cursor)
                _LOG.info(
                    "event=historical_poll symbols=%s returned=%d emitted=%d "
                    "real=%d interpolated=%d end=%s",
                    ",".join(normalized),
                    len(bars),
                    emitted,
                    sum(not bar.interpolated for bar in bars),
                    sum(bar.interpolated for bar in bars),
                    end.isoformat(),
                )

            await self._sleep(self._poll_seconds)
