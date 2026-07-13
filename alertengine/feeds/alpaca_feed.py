"""Real 1-min bar feed over Alpaca's websocket stream.

Alpaca delivers 1-min bars (no native 2-min stream); the engine aggregates them
to 2-min via BarAggregator. Alpaca's client is callback-based and its public
`run()` spins its own event loop, so we bridge its callback into our async
generator with a queue and drive its internal `_run_forever()` coroutine as a
task on the current loop. (`_run_forever` is what alpaca-py's own `run()` awaits;
if a future version changes it, the fallback is to run `stream.run()` in a
thread and hand bars back via `loop.call_soon_threadsafe`.)

Uses the free IEX feed by default (fine for a human-checked alert). Credentials
come from ALPACA_API_KEY / ALPACA_SECRET_KEY (load a .env before constructing).
"""

import asyncio
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from alpaca.data.enums import DataFeed as AlpacaDataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from ..interfaces import DataFeed
from ..models import Bar


class AlpacaFeed(DataFeed):
    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        feed: AlpacaDataFeed = AlpacaDataFeed.IEX,
    ) -> None:
        key = api_key or os.environ.get("ALPACA_API_KEY")
        secret = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError(
                "Missing Alpaca credentials: set ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY (e.g. in a .env file)."
            )
        self._key = key
        self._secret = secret
        self._feed = feed
        self._hist: StockHistoricalDataClient | None = None  # built lazily

    @staticmethod
    def _to_bar(abar) -> Bar:
        """Map an Alpaca bar to our Bar. Alpaca's timestamp is the bar START."""
        return Bar(
            symbol=abar.symbol,
            timestamp=abar.timestamp,
            open=abar.open,
            high=abar.high,
            low=abar.low,
            close=abar.close,
            volume=abar.volume,
        )

    def backfill_bars(self, symbols: list[str], minutes: int = 180) -> list[Bar]:
        """Recent 1-min bars (up to `minutes` back) over REST, for warm-up
        seeding. The live websocket only emits going forward, so on (re)start the
        engine uses this to pre-fill its 2-min history — otherwise the rule waits
        ~40 min for a full Bollinger/RSI window to accumulate before it can fire.

        Off-hours or over a weekend this returns the tail of the last trading
        day (its most recent real closes), which is the right warm-up baseline —
        exactly the history a continuously-running process would already hold.
        Bars are merged across symbols in true chronological order, as the live
        stream would deliver them. Empty list if none exist (e.g. cold at open).
        """
        if self._hist is None:
            self._hist = StockHistoricalDataClient(self._key, self._secret)
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        req = StockBarsRequest(
            symbol_or_symbols=[s.upper() for s in symbols],
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=self._feed,
        )
        barset = self._hist.get_stock_bars(req)
        bars: list[Bar] = []
        for sym in symbols:
            for abar in barset.data.get(sym.upper(), []):
                bars.append(self._to_bar(abar))
        bars.sort(key=lambda b: b.timestamp)
        return bars

    def fetch_closes(
        self, symbols: list[str], hours: int, lookback_days: int
    ) -> dict[str, list[float]]:
        """Historical closes per symbol at an arbitrary hourly timeframe, oldest
        to newest. For the off-hours multi-timeframe pre-screen (RSI on 4h/1h
        bars). One batched REST request covers all symbols; Alpaca returns bars
        already in chronological order. Missing symbols map to an empty list.
        """
        if self._hist is None:
            self._hist = StockHistoricalDataClient(self._key, self._secret)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        req = StockBarsRequest(
            symbol_or_symbols=[s.upper() for s in symbols],
            timeframe=TimeFrame(hours, TimeFrameUnit.Hour),
            start=start,
            end=end,
            feed=self._feed,
        )
        barset = self._hist.get_stock_bars(req)
        return {
            s.upper(): [b.close for b in barset.data.get(s.upper(), [])]
            for s in symbols
        }

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        # Build a fresh client for every subscription. The remote controller
        # restarts this generator when /watch or /unwatch changes the symbols;
        # alpaca-py's stopped websocket object is not safely reusable.
        stream = StockDataStream(self._key, self._secret, feed=self._feed)
        queue: asyncio.Queue = asyncio.Queue()

        async def handler(abar) -> None:
            await queue.put(abar)

        stream.subscribe_bars(handler, *[s.upper() for s in symbols])
        run_task = asyncio.create_task(stream._run_forever())
        get_task: asyncio.Task | None = None
        try:
            while True:
                get_task = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    (get_task, run_task), return_when=asyncio.FIRST_COMPLETED
                )
                if get_task in done:
                    # Preserve a final queued bar even if the websocket runner
                    # finishes in the same event-loop turn. Its exit is handled
                    # on the next iteration.
                    abar = get_task.result()
                    get_task = None
                    yield self._to_bar(abar)
                    continue

                if run_task in done:
                    # Without this branch a dead Alpaca websocket leaves the
                    # consumer blocked on queue.get() forever. Let the
                    # WatchController supervisor log/retry the stream instead.
                    get_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await get_task
                    get_task = None
                    if run_task.cancelled():
                        raise RuntimeError("Alpaca websocket task stopped unexpectedly")
                    error = run_task.exception()
                    if error is not None:
                        raise error
                    return
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()
                with suppress(asyncio.CancelledError):
                    await get_task
            with suppress(Exception):
                await stream.stop_ws()
            if not run_task.done():
                run_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await run_task
