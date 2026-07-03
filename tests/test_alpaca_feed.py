"""Unit tests for AlpacaFeed's pure parts (no websocket, no network).

Live streaming needs real keys + market hours and is verified out-of-band; here
we cover the credential guard and the Alpaca-bar -> Bar mapping.
"""

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
    assert (bar.symbol, bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        "AAPL", ts, 1.0, 2.0, 0.5, 1.5, 1000,
    )


def test_construct_with_explicit_keys_does_not_connect():
    # Constructing must not open a socket; just holds a configured stream client.
    feed = AlpacaFeed(api_key="fake", secret_key="fake")
    assert feed._stream is not None
