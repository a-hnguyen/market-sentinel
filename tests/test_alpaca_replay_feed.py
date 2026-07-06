"""Unit tests for AlpacaReplayFeed's pure parts (no REST, no network).

Live pulls hit Alpaca's REST API and are verified out-of-band; here we cover the
credential guard, the Alpaca-bar -> Bar mapping, and the chronological merge that
`_fetch` performs across symbols.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alertengine.feeds.alpaca_replay_feed import AlpacaReplayFeed
from alertengine.models import Bar


def _abar(symbol, minute, close):
    ts = datetime(2026, 1, 2, 14, minute, tzinfo=timezone.utc)
    return SimpleNamespace(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Alpaca credentials"):
        AlpacaReplayFeed()


def test_to_bar_maps_alpaca_bar():
    ts = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    abar = SimpleNamespace(
        symbol="AAPL", timestamp=ts, open=1.0, high=2.0, low=0.5, close=1.5, volume=1000
    )
    bar = AlpacaReplayFeed._to_bar(abar)
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
    feed = AlpacaReplayFeed(api_key="fake", secret_key="fake")
    assert feed._client is not None


def test_fetch_merges_symbols_into_chronological_order():
    feed = AlpacaReplayFeed(api_key="fake", secret_key="fake")
    # Two symbols, interleaved in time; the barset returns them grouped by symbol.
    barset = SimpleNamespace(
        data={
            "AAPL": [_abar("AAPL", 30, 100.0), _abar("AAPL", 33, 101.0)],
            "TSLA": [_abar("TSLA", 31, 200.0), _abar("TSLA", 32, 201.0)],
        }
    )
    feed._client = SimpleNamespace(get_stock_bars=lambda req: barset)

    bars = feed._fetch(["AAPL", "TSLA"])
    # Sorted across symbols by timestamp: 30, 31, 32, 33.
    assert [(b.symbol, b.timestamp.minute) for b in bars] == [
        ("AAPL", 30),
        ("TSLA", 31),
        ("TSLA", 32),
        ("AAPL", 33),
    ]


def test_stream_bars_yields_all_fetched_bars():
    feed = AlpacaReplayFeed(api_key="fake", secret_key="fake")
    barset = SimpleNamespace(
        data={"AAPL": [_abar("AAPL", 30, 100.0), _abar("AAPL", 31, 99.0)]}
    )
    feed._client = SimpleNamespace(get_stock_bars=lambda req: barset)

    async def _collect():
        return [b async for b in feed.stream_bars(["AAPL"])]

    bars = asyncio.run(_collect())
    assert [b.close for b in bars] == [100.0, 99.0]
    assert all(isinstance(b, Bar) for b in bars)
