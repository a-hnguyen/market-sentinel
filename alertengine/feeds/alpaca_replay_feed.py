"""Historical 1-min bar replay over Alpaca's REST API.

The live websocket feed only emits while the market is open. This feed pulls a
recent trading day's 1-min bars over REST (which works any time — nights,
weekends, holidays) and replays them chronologically through the *same*
DataFeed seam, so the full aggregator -> indicators -> rule -> notifier path
runs on real market data with no waiting for market hours.

Bars from all requested symbols are merged and sorted by timestamp, so they
arrive interleaved in true chronological order — exactly as the live stream
would deliver them. Uses the free IEX feed; credentials come from
ALPACA_API_KEY / ALPACA_SECRET_KEY (load a .env before constructing).
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from alpaca.data.enums import DataFeed as AlpacaDataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from ..interfaces import DataFeed
from ..models import Bar


class AlpacaReplayFeed(DataFeed):
    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        lookback_days: int = 5,
        interval: float = 0.0,
        feed: AlpacaDataFeed = AlpacaDataFeed.IEX,
    ) -> None:
        """`lookback_days` is the calendar window pulled (enough to span at least
        one trading day, since weekends/holidays have no bars). `interval` is the
        async delay between successive bars (0 replays as fast as possible; a
        small >0 makes bars stream visibly in the REPL).
        """
        key = api_key or os.environ.get("ALPACA_API_KEY")
        secret = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError(
                "Missing Alpaca credentials: set ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY (e.g. in a .env file)."
            )
        self._client = StockHistoricalDataClient(key, secret)
        self._lookback_days = lookback_days
        self._interval = interval
        self._feed = feed

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

    def _fetch(self, symbols: list[str]) -> list[Bar]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=self._lookback_days)
        req = StockBarsRequest(
            symbol_or_symbols=[s.upper() for s in symbols],
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            feed=self._feed,
        )
        barset = self._client.get_stock_bars(req)
        bars: list[Bar] = []
        for sym in symbols:
            for abar in barset.data.get(sym.upper(), []):
                bars.append(self._to_bar(abar))
        # Merge across symbols into true chronological order, as live would arrive.
        bars.sort(key=lambda b: b.timestamp)
        return bars

    def describe_window(self) -> str:
        """Human-readable date range the replay pulls from (the trailing
        `lookback_days` calendar window). Shown in the `screen` output."""
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=self._lookback_days)
        return (
            f"Replay data: real Alpaca IEX 1-min bars, {start} → {end} "
            "(watch replays the trading days in this range)\n"
        )

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        # REST call is blocking; run it off the event loop.
        bars = await asyncio.to_thread(self._fetch, symbols)
        for bar in bars:
            yield bar
            if self._interval:
                await asyncio.sleep(self._interval)
