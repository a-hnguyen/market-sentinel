"""Real 1-min bar feed over Alpaca's websocket stream.

Alpaca delivers 1-min bars (no native 2-min stream); the engine aggregates them
to 2-min via BarAggregator. Alpaca's client is callback-based and its public
`run()` spins its own event loop, so we bridge its callback into our async
generator with a queue. We intentionally run one connection attempt with the
pinned client's connection primitives instead of `_run_forever()`: alpaca-py
0.43 retries connection-limit failures with no delay, which can busy-loop and
starve the Discord event loop. WatchController owns the retry backoff instead.

Uses the free IEX feed by default (fine for a human-checked alert). Credentials
come from ALPACA_API_KEY / ALPACA_SECRET_KEY (load a .env before constructing).
"""

import asyncio
import os
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from alpaca.data.enums import DataFeed as AlpacaDataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from requests import Session
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

from ..interfaces import DataFeed
from ..models import Bar

_HISTORICAL_BATCH_SIZE = 20
_HISTORICAL_CONNECT_TIMEOUT = 5
_HISTORICAL_READ_TIMEOUT = 45
_HISTORICAL_ATTEMPTS = 2
_STREAM_CLOSE_TIMEOUT = 5


class _BoundedSession(Session):
    """requests session that never permits Alpaca HTTP calls to wait forever."""

    def request(self, method, url, **kwargs):
        kwargs.setdefault(
            "timeout", (_HISTORICAL_CONNECT_TIMEOUT, _HISTORICAL_READ_TIMEOUT)
        )
        return super().request(method, url, **kwargs)


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
        # Canceling asyncio.to_thread() cannot stop its underlying thread. Keep
        # canceled/restarted watchers from issuing overlapping REST backfills.
        self._historical_lock = threading.Lock()

    def _historical_client(self) -> StockHistoricalDataClient:
        if self._hist is None:
            self._hist = StockHistoricalDataClient(self._key, self._secret)
            # alpaca-py 0.43 does not pass a timeout to requests. Its session is
            # private, but the package is pinned and our websocket adapter
            # already relies on a pinned private API for the same reason.
            self._hist._session = _BoundedSession()
        return self._hist

    def _request_bars(self, request, label: str):
        """Issue one bounded historical request, retrying transient I/O once."""
        for attempt in range(1, _HISTORICAL_ATTEMPTS + 1):
            try:
                return self._historical_client().get_stock_bars(request)
            except (RequestsTimeout, RequestsConnectionError) as exc:
                if attempt == _HISTORICAL_ATTEMPTS:
                    raise RuntimeError(
                        f"Alpaca {label} failed after {attempt} attempts: {exc}"
                    ) from exc
                print(
                    f"Alpaca {label} attempt {attempt} failed; retrying: {exc}",
                    flush=True,
                )
                time.sleep(2)

    @staticmethod
    def _batches(symbols: list[str]) -> list[list[str]]:
        normalized = [symbol.upper() for symbol in symbols]
        return [
            normalized[index : index + _HISTORICAL_BATCH_SIZE]
            for index in range(0, len(normalized), _HISTORICAL_BATCH_SIZE)
        ]

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
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        bars: list[Bar] = []
        batches = self._batches(symbols)
        with self._historical_lock:
            for index, batch in enumerate(batches, 1):
                print(
                    f"backfill: batch {index}/{len(batches)} "
                    f"({len(batch)} symbols)",
                    flush=True,
                )
                req = StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Minute,
                    start=start,
                    end=end,
                    feed=self._feed,
                )
                barset = self._request_bars(req, f"backfill batch {index}")
                for sym in batch:
                    for abar in barset.data.get(sym, []):
                        bars.append(self._to_bar(abar))
        bars.sort(key=lambda b: b.timestamp)
        return bars

    def fetch_closes(
        self, symbols: list[str], hours: int, lookback_days: int
    ) -> dict[str, list[float]]:
        """Historical closes per symbol at an arbitrary hourly timeframe, oldest
        to newest. For the off-hours multi-timeframe pre-screen (RSI on 4h/1h
        bars). Small sequential REST batches cap Alpaca pagination and memory;
        missing symbols map to an empty list.
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        closes = {symbol.upper(): [] for symbol in symbols}
        batches = self._batches(symbols)
        with self._historical_lock:
            for index, batch in enumerate(batches, 1):
                print(
                    f"pre-screen {hours}h: batch {index}/{len(batches)} "
                    f"({len(batch)} symbols)",
                    flush=True,
                )
                req = StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame(hours, TimeFrameUnit.Hour),
                    start=start,
                    end=end,
                    feed=self._feed,
                )
                barset = self._request_bars(req, f"{hours}h pre-screen batch {index}")
                for symbol in batch:
                    closes[symbol] = [bar.close for bar in barset.data.get(symbol, [])]
        return closes

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        # Build a fresh client for every subscription. The remote controller
        # restarts this generator when /watch or /unwatch changes the symbols;
        # alpaca-py's stopped websocket object is not safely reusable.
        stream = StockDataStream(self._key, self._secret, feed=self._feed)
        queue: asyncio.Queue = asyncio.Queue()

        async def handler(abar) -> None:
            await queue.put(abar)

        stream.subscribe_bars(handler, *[s.upper() for s in symbols])

        async def run_once() -> None:
            """Run one socket attempt and let failures reach our supervisor."""
            stream._loop = asyncio.get_running_loop()
            stream._should_run = True
            stream._running = False
            try:
                await stream._start_ws()
                await stream._send_subscribe_msg()
                stream._running = True
                await stream._consume()
            finally:
                try:
                    await asyncio.wait_for(
                        stream.close(), timeout=_STREAM_CLOSE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    print("Alpaca websocket close timed out; abandoning it", flush=True)
                except Exception:
                    pass

        run_task = asyncio.create_task(run_once())
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
            if not run_task.done():
                run_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await run_task
