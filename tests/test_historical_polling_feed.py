import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from alertengine.feeds.historical_polling_feed import (
    HistoricalPollingFeed,
    completed_minute,
)
from alertengine.interfaces import HistoricalBarFeed
from alertengine.models import Bar

BASE = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)


def bar(minute, symbol="AMC", *, interpolated=False):
    price = 2.5 + minute / 100
    return Bar(
        symbol=symbol,
        timestamp=BASE + timedelta(minutes=minute),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=0 if interpolated else 10,
        interpolated=interpolated,
        session="overnight",
    )


class FakeHistoricalFeed(HistoricalBarFeed):
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def fetch_bars(self, symbols, start, end):
        self.requests.append((list(symbols), start, end))
        return list(self.responses.pop(0))


class Clock:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


async def no_sleep(_seconds):
    return None


def test_completed_minute_is_exclusive_utc_boundary():
    assert completed_minute(
        datetime(2026, 8, 6, 17, 5, 42, 123, tzinfo=timezone(timedelta(hours=-7)))
    ) == datetime(2026, 8, 7, 0, 5, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="timezone-aware"):
        completed_minute(datetime(2026, 8, 7, 0, 5))


def test_backfill_uses_completed_minute_and_configured_lookback():
    source = FakeHistoricalFeed([[bar(1)]])
    feed = HistoricalPollingFeed(
        source,
        backfill_minutes=60,
        clock=lambda: BASE + timedelta(hours=2, seconds=30),
    )

    result = asyncio.run(feed.backfill_bars(["AMC"]))

    assert result == [bar(1)]
    assert source.requests == [
        (["AMC"], BASE + timedelta(hours=1), BASE + timedelta(hours=2))
    ]


async def collect_two(feed):
    stream = feed.stream_bars(["amc"])
    try:
        return [await anext(stream), await anext(stream)]
    finally:
        await stream.aclose()


def test_polling_starts_at_current_2min_bucket_and_deduplicates_overlap():
    # At 00:05:30, 00:04 is complete and belongs to the current 00:04 bucket;
    # 00:05 is still forming. The second poll at 00:06:05 may return the 00:04
    # overlap again, but only the newly completed 00:05 bar is emitted.
    source = FakeHistoricalFeed(
        [
            [bar(3), bar(4), bar(5)],
            [bar(4), bar(5), bar(6)],
        ]
    )
    feed = HistoricalPollingFeed(
        source,
        overlap_minutes=3,
        clock=Clock(
            [
                BASE + timedelta(minutes=5, seconds=30),
                BASE + timedelta(minutes=6, seconds=5),
            ]
        ),
        sleep=no_sleep,
    )

    emitted = asyncio.run(collect_two(feed))

    assert [item.timestamp for item in emitted] == [
        BASE + timedelta(minutes=4),
        BASE + timedelta(minutes=5),
    ]
    assert all(
        item.timestamp < request[2] for item, request in zip(emitted, source.requests)
    )
    assert source.requests[0] == (
        ["AMC"],
        BASE,
        BASE + timedelta(minutes=5),
    )


def test_polling_replays_dropped_bucket_when_starting_after_even_minute():
    source = FakeHistoricalFeed([[bar(3), bar(4), bar(5)]])
    feed = HistoricalPollingFeed(
        source,
        overlap_minutes=0,
        clock=lambda: BASE + timedelta(minutes=6, seconds=5),
        sleep=no_sleep,
    )

    async def collect():
        stream = feed.stream_bars(["AMC"])
        try:
            return [await anext(stream), await anext(stream)]
        finally:
            await stream.aclose()

    emitted = asyncio.run(collect())

    assert [item.timestamp for item in emitted] == [
        BASE + timedelta(minutes=4),
        BASE + timedelta(minutes=5),
    ]
    assert source.requests[0] == (
        ["AMC"],
        BASE + timedelta(minutes=3),
        BASE + timedelta(minutes=6),
    )


def test_polling_rejects_empty_symbol_list():
    source = FakeHistoricalFeed([])
    feed = HistoricalPollingFeed(source)

    async def consume():
        await anext(feed.stream_bars([]))

    with pytest.raises(ValueError, match="symbols must not be empty"):
        asyncio.run(consume())
