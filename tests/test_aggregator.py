"""Tests for the 1-min -> 2-min aggregator. This must pass before building on top."""

from datetime import datetime, timedelta

from alertengine.aggregator import BarAggregator, bucket_start
from alertengine.models import Bar


def bar(
    sym: str, minute: int, o: float, h: float, low: float, c: float, v: float
) -> Bar:
    """Build a 1-min bar at 2026-01-02 09:<minute>."""
    return Bar(
        symbol=sym,
        timestamp=datetime(2026, 1, 2, 9, minute),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
    )


def test_bucket_start_aligns_to_even_minute():
    assert bucket_start(datetime(2026, 1, 2, 9, 30)) == datetime(2026, 1, 2, 9, 30)
    assert bucket_start(datetime(2026, 1, 2, 9, 31)) == datetime(2026, 1, 2, 9, 30)
    assert bucket_start(datetime(2026, 1, 2, 9, 32)) == datetime(2026, 1, 2, 9, 32)
    # seconds/micros are dropped
    assert bucket_start(datetime(2026, 1, 2, 9, 31, 45, 123)) == datetime(
        2026, 1, 2, 9, 30
    )


def test_two_bars_merge_into_one_2min_bar():
    agg = BarAggregator()
    # 09:30 and 09:31 belong to the same 09:30 bucket.
    assert agg.add(bar("AAA", 30, o=10, h=11, low=9, c=10.5, v=100)) is None
    assert agg.add(bar("AAA", 31, o=10.5, h=12, low=10, c=11.5, v=200)) is None

    # A bar for the next bucket (09:32) flushes the completed 09:30 bar.
    out = agg.add(bar("AAA", 32, o=11.5, h=11.6, low=11.4, c=11.5, v=50))
    assert out is not None
    assert out.timestamp == datetime(2026, 1, 2, 9, 30)  # bucket START
    assert out.open == 10  # first bar's open
    assert out.high == 12  # max of highs
    assert out.low == 9  # min of lows
    assert out.close == 11.5  # last bar's close
    assert out.volume == 300  # sum of volumes


def test_missing_minute_emits_single_bar_bucket():
    """IEX may skip a quiet minute; a one-bar bucket must still emit, not stall."""
    agg = BarAggregator()
    # Only 09:30 arrives for its bucket (09:31 missing).
    assert agg.add(bar("BBB", 30, o=5, h=6, low=4, c=5.5, v=80)) is None
    # 09:32 (next bucket) advances -> the single-bar 09:30 bucket is emitted as-is.
    out = agg.add(bar("BBB", 32, o=5.5, h=5.6, low=5.4, c=5.5, v=10))
    assert out is not None
    assert out.timestamp == datetime(2026, 1, 2, 9, 30)
    assert (out.open, out.high, out.low, out.close, out.volume) == (5, 6, 4, 5.5, 80)


def test_symbols_are_independent():
    agg = BarAggregator()
    agg.add(bar("AAA", 30, o=10, h=10, low=10, c=10, v=1))
    agg.add(bar("BBB", 30, o=20, h=20, low=20, c=20, v=2))
    agg.add(bar("AAA", 31, o=10, h=15, low=8, c=12, v=3))
    agg.add(bar("BBB", 31, o=20, h=25, low=18, c=22, v=4))

    aaa = agg.flush("AAA")
    bbb = agg.flush("BBB")
    assert aaa.symbol == "AAA" and (aaa.high, aaa.low, aaa.close, aaa.volume) == (
        15,
        8,
        12,
        4,
    )
    assert bbb.symbol == "BBB" and (bbb.high, bbb.low, bbb.close, bbb.volume) == (
        25,
        18,
        22,
        6,
    )


def test_flush_emits_pending_bucket_and_clears():
    agg = BarAggregator()
    agg.add(bar("AAA", 30, o=1, h=2, low=1, c=2, v=10))
    out = agg.flush("AAA")
    assert out is not None and out.timestamp == datetime(2026, 1, 2, 9, 30)
    # Nothing left pending.
    assert agg.flush("AAA") is None


def test_flush_all_emits_every_symbol():
    agg = BarAggregator()
    agg.add(bar("AAA", 30, o=1, h=2, low=1, c=2, v=10))
    agg.add(bar("BBB", 30, o=3, h=4, low=3, c=4, v=20))
    out = agg.flush_all()
    assert {b.symbol for b in out} == {"AAA", "BBB"}
    assert agg.flush_all() == []


def test_long_run_emits_one_bar_per_bucket():
    """Feed 09:30..09:35 (three full buckets) and confirm boundaries."""
    agg = BarAggregator()
    emitted = []
    base = datetime(2026, 1, 2, 9, 30)
    for i in range(6):  # minutes 30..35
        ts = base + timedelta(minutes=i)
        out = agg.add(Bar("AAA", ts, open=i, high=i + 1, low=i - 1, close=i, volume=1))
        if out is not None:
            emitted.append(out)
    emitted.append(agg.flush("AAA"))

    starts = [b.timestamp for b in emitted]
    assert starts == [
        datetime(2026, 1, 2, 9, 30),
        datetime(2026, 1, 2, 9, 32),
        datetime(2026, 1, 2, 9, 34),
    ]
