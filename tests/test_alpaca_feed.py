"""Unit tests for AlpacaFeed's pure parts (no websocket, no network).

Live streaming needs real keys + market hours and is verified out-of-band; here
we cover the credential guard and the Alpaca-bar -> Bar mapping.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alertengine.feeds.alpaca_feed import AlpacaFeed
from alertengine.models import Bar


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Alpaca credentials"):
        AlpacaFeed()


def test_to_bar_maps_alpaca_bar():
    ts = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    abar = SimpleNamespace(
        symbol="AAPL", timestamp=ts, open=1.0, high=2.0, low=0.5, close=1.5, volume=1000
    )
    bar = AlpacaFeed._to_bar(abar)
    assert isinstance(bar, Bar)
    assert (
        bar.symbol,
        bar.timestamp,
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume,
    ) == (
        "AAPL",
        ts,
        1.0,
        2.0,
        0.5,
        1.5,
        1000,
    )


def test_construct_with_explicit_keys_does_not_connect():
    # Constructing must not open a socket; clients are made per subscription so
    # the Discord controller can safely restart after watchlist changes.
    feed = AlpacaFeed(api_key="fake", secret_key="fake")
    assert feed._key == "fake"
    assert feed._secret == "fake"


def test_stream_propagates_websocket_failure(monkeypatch):
    class _DeadStream:
        def __init__(self, *args, **kwargs):
            pass

        def subscribe_bars(self, handler, *symbols):
            self.handler = handler

        async def _run_forever(self):
            await asyncio.sleep(0)
            raise RuntimeError("socket died")

        async def stop_ws(self):
            pass

    monkeypatch.setattr("alertengine.feeds.alpaca_feed.StockDataStream", _DeadStream)
    feed = AlpacaFeed(api_key="fake", secret_key="fake")

    async def drive():
        stream = feed.stream_bars(["AAPL"])
        with pytest.raises(RuntimeError, match="socket died"):
            await stream.__anext__()

    asyncio.run(drive())


def test_stream_yields_final_queued_bar_before_clean_exit(monkeypatch):
    bar = _abar("AAPL", 30, 123.45)

    class _OneBarStream:
        def __init__(self, *args, **kwargs):
            pass

        def subscribe_bars(self, handler, *symbols):
            self.handler = handler

        async def _run_forever(self):
            await self.handler(bar)

        async def stop_ws(self):
            pass

    monkeypatch.setattr("alertengine.feeds.alpaca_feed.StockDataStream", _OneBarStream)
    feed = AlpacaFeed(api_key="fake", secret_key="fake")

    async def drive():
        stream = feed.stream_bars(["AAPL"])
        assert (await stream.__anext__()).close == 123.45
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()

    asyncio.run(drive())


def _abar(symbol, minute, close):
    ts = datetime(2026, 1, 2, 14, minute, tzinfo=timezone.utc)
    return SimpleNamespace(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=10,
    )


class _FakeHistClient:
    """Stands in for StockHistoricalDataClient; records the request, returns a
    fixed barset. Bars are deliberately out of chronological order per symbol."""

    def __init__(self, data):
        self._data = data
        self.request = None

    def get_stock_bars(self, req):
        self.request = req
        return SimpleNamespace(data=self._data)


def test_backfill_bars_maps_and_sorts_chronologically():
    feed = AlpacaFeed(api_key="fake", secret_key="fake")
    # Two symbols, each unsorted; the merged result must be globally sorted.
    feed._hist = _FakeHistClient(
        {
            "AAPL": [_abar("AAPL", 32, 1.0), _abar("AAPL", 30, 2.0)],
            "MSFT": [_abar("MSFT", 31, 3.0)],
        }
    )
    bars = feed.backfill_bars(["aapl", "msft"], minutes=60)

    assert all(isinstance(b, Bar) for b in bars)
    # Globally merged in true chronological order (30, 31, 32), as the live
    # stream would deliver them.
    assert [b.timestamp.minute for b in bars] == [30, 31, 32]
    assert [b.symbol for b in bars] == ["AAPL", "MSFT", "AAPL"]
    # Request was built for 1-min bars on the upper-cased symbols.
    req = feed._hist.request
    assert set(req.symbol_or_symbols) == {"AAPL", "MSFT"}


def test_backfill_bars_empty_when_no_data():
    feed = AlpacaFeed(api_key="fake", secret_key="fake")
    feed._hist = _FakeHistClient({})
    assert feed.backfill_bars(["AAPL"]) == []


def test_fetch_closes_returns_close_series_per_symbol():
    feed = AlpacaFeed(api_key="fake", secret_key="fake")
    feed._hist = _FakeHistClient(
        {
            "AAPL": [_abar("AAPL", 0, 10.0), _abar("AAPL", 4, 11.0)],
            "MSFT": [_abar("MSFT", 0, 20.0)],
        }
    )
    closes = feed.fetch_closes(["aapl", "msft", "none"], hours=4, lookback_days=90)
    assert closes == {"AAPL": [10.0, 11.0], "MSFT": [20.0], "NONE": []}
    # Request used a 4-hour timeframe on the upper-cased symbols.
    req = feed._hist.request
    assert req.timeframe.amount_value == 4
    assert set(req.symbol_or_symbols) == {"AAPL", "MSFT", "NONE"}
