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
from typing import AsyncIterator

from alpaca.data.enums import DataFeed as AlpacaDataFeed
from alpaca.data.live import StockDataStream

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
        self._stream = StockDataStream(key, secret, feed=feed)

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

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        queue: asyncio.Queue = asyncio.Queue()

        async def handler(abar) -> None:
            await queue.put(abar)

        self._stream.subscribe_bars(handler, *[s.upper() for s in symbols])
        run_task = asyncio.create_task(self._stream._run_forever())
        try:
            while True:
                abar = await queue.get()
                yield self._to_bar(abar)
        finally:
            run_task.cancel()
            with suppress(Exception):
                await self._stream.stop_ws()
