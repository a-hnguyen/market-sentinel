import asyncio
from datetime import datetime, timezone

import pytest

from alertengine.feeds.robinhood_feed import RobinhoodHistoricalFeed


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def get_equity_historicals(self, request):
        self.requests.append(dict(request))
        return self.responses.pop(0)


def response(symbol, bars=None):
    return {
        "data": {
            "results": [
                {
                    "symbol": symbol,
                    "interval": "minute",
                    "bounds": "24_5",
                    "bars": bars or [],
                }
            ]
        }
    }


def raw_bar(timestamp="2026-08-07T00:02:00Z", *, interpolated=True):
    payload = {
        "begins_at": timestamp,
        "open_price": "2.620000",
        "high_price": "2.630000",
        "low_price": "2.610000",
        "close_price": "2.620000",
        "volume": 0 if interpolated else 40,
        "session": "overnight",
    }
    if interpolated:
        payload["interpolated"] = True
    return payload


def test_fetch_bars_uses_read_only_minute_24_5_request_and_normalizes():
    transport = FakeTransport([response("AMC", [raw_bar()])])
    feed = RobinhoodHistoricalFeed(transport)

    bars = asyncio.run(
        feed.fetch_bars(
            ["amc"],
            datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 7, 0, 5, tzinfo=timezone.utc),
        )
    )

    assert transport.requests == [
        {
            "symbols": ["AMC"],
            "start_time": "2026-08-07T00:00:00Z",
            "end_time": "2026-08-07T00:05:00Z",
            "interval": "minute",
            "bounds": "24_5",
            "adjustment_type": "split",
        }
    ]
    assert len(bars) == 1
    bar = bars[0]
    assert bar.symbol == "AMC"
    assert bar.timestamp == datetime(2026, 8, 7, 0, 2, tzinfo=timezone.utc)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        2.62,
        2.63,
        2.61,
        2.62,
        0,
    )
    assert bar.interpolated is True
    assert bar.session == "overnight"


def test_fetch_bars_batches_ten_symbols_and_sorts_across_responses():
    symbols = [f"S{i}" for i in range(11)]
    transport = FakeTransport(
        [
            response("S0", [raw_bar("2026-08-07T00:02:00Z", interpolated=False)]),
            response("S10", [raw_bar("2026-08-07T00:01:00Z", interpolated=False)]),
        ]
    )
    feed = RobinhoodHistoricalFeed(transport)

    bars = asyncio.run(
        feed.fetch_bars(
            symbols,
            datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 7, 0, 5, tzinfo=timezone.utc),
        )
    )

    assert [request["symbols"] for request in transport.requests] == [
        symbols[:10],
        symbols[10:],
    ]
    assert [bar.symbol for bar in bars] == ["S10", "S0"]


def test_fetch_bars_requires_aware_ordered_time_range():
    feed = RobinhoodHistoricalFeed(FakeTransport([]))
    aware = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(feed.fetch_bars(["AMC"], datetime(2026, 8, 7), aware))
    with pytest.raises(ValueError, match="start must be before end"):
        asyncio.run(feed.fetch_bars(["AMC"], aware, aware))


def test_fetch_bars_rejects_malformed_response():
    feed = RobinhoodHistoricalFeed(FakeTransport([{}]))

    with pytest.raises(ValueError, match="missing data"):
        asyncio.run(
            feed.fetch_bars(
                ["AMC"],
                datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 8, 7, 0, 5, tzinfo=timezone.utc),
            )
        )
