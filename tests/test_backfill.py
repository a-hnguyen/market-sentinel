"""Backfill-on-startup: the engine seeds 2-min history from recent REST bars so
the rule has a full Bollinger/RSI window immediately, instead of waiting ~40 min
for live bars to accumulate. No network — a fake feed supplies synthetic bars.

Uses asyncio.run rather than a pytest-asyncio marker so it runs with plain
pytest (no extra plugin).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import DataFeed, Notifier
from alertengine.models import Alert, Bar
from alertengine.rules.bb_rsi_rule import BBRSIRule
from alertengine.screeners.mock_screener import MockScreener

# 14:00 UTC sits on a 2-min clock boundary, so consecutive-minute pairs
# aggregate into clean 2-min buckets.
BASE = datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc)

# A 20-close series (flat, then a sharp multi-bar drop) that trips layer-1:
# last close 63 < lower BB 72.06 and RSI 0.0 < 30. Verified against the rule.
FIRING = [
    100,
    100,
    100,
    100,
    100,
    100,
    100,
    100,
    100,
    100,
    100,
    99,
    98,
    96,
    93,
    89,
    84,
    78,
    71,
    63,
]


def _minute_bars(bucket_closes, start_bucket=0, symbol="ZZ"):
    """Two identical 1-min bars per bucket, so each aggregated 2-min bar closes
    at the given value. `start_bucket` places them on distinct 2-min boundaries.
    """
    bars = []
    for offset, close in enumerate(bucket_closes):
        bi = start_bucket + offset
        for minute in (0, 1):
            ts = BASE + timedelta(minutes=bi * 2 + minute)
            bars.append(Bar(symbol, ts, close, close, close, close, 1000))
    return bars


class _RecordingNotifier(Notifier):
    def __init__(self):
        self.alerts: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


class ColdFeed(DataFeed):
    """A feed with no warm-up capability: it only streams (no backfill_bars)."""

    def __init__(self, stream_1min):
        self._stream_1min = stream_1min

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        for bar in self._stream_1min:
            yield bar


class WarmFeed(ColdFeed):
    """Adds the backfill seam the live Alpaca feed exposes."""

    def __init__(self, backfill_1min, stream_1min):
        super().__init__(stream_1min)
        self._backfill_1min = backfill_1min

    def backfill_bars(self, symbols: list[str]) -> list[Bar]:
        return list(self._backfill_1min)


def _engine(feed, notifier):
    return AlertEngine(
        screener=MockScreener(),
        feed=feed,
        rule=BBRSIRule(),
        notifier=notifier,
        gate=ApprovalGate(),
    )


def test_backfill_seeds_history_without_firing():
    # 42 buckets of steadily declining closes -> 41 completed 2-min bars seeded.
    # Even though the seed is deeply oversold, the rule is never evaluated on it,
    # so NO alert fires during warm-up.
    seed = _minute_bars([100 - i for i in range(42)])
    notifier = _RecordingNotifier()
    engine = _engine(WarmFeed(backfill_1min=seed, stream_1min=[]), notifier)

    asyncio.run(engine.watch(["ZZ"]))

    st = engine.status()["symbols"]["ZZ"]
    assert st["history"] == 41  # trailing in-progress bucket is dropped
    assert st["bars_seen"] == 0  # rule ran on zero seeded bars
    assert notifier.alerts == []  # warm-up is silent


def test_no_backfill_method_is_noop():
    # A feed without backfill_bars must not error; nothing gets seeded.
    notifier = _RecordingNotifier()
    engine = _engine(ColdFeed(stream_1min=[]), notifier)

    asyncio.run(engine.watch(["ZZ"]))

    assert engine.status()["symbols"] == {}
    assert notifier.alerts == []


def test_warmup_enables_immediate_alert():
    # Seed the first 19 closes (+ a throwaway 20th bucket that stays in-progress
    # and is dropped), then stream the 20th close live. History reaches 20 = the
    # rule's warm-up, so the very first live bar fires.
    seed = _minute_bars(FIRING[:19] + [999], start_bucket=0)
    live = _minute_bars([FIRING[19]], start_bucket=20)
    notifier = _RecordingNotifier()
    engine = _engine(WarmFeed(backfill_1min=seed, stream_1min=live), notifier)

    asyncio.run(engine.watch(["ZZ"]))

    assert len(notifier.alerts) == 1
    a = notifier.alerts[0]
    assert a.symbol == "ZZ"
    assert a.context["close"] < a.context["bb_lower"]
    assert a.context["rsi"] < 30


def test_cold_start_misses_the_same_bar():
    # Same single live bar, but no warm-up: history is only 1 bar, far below the
    # rule's 20-bar minimum, so it CANNOT fire. This is exactly the gap backfill
    # closes.
    live = _minute_bars([FIRING[19]], start_bucket=20)
    notifier = _RecordingNotifier()
    engine = _engine(ColdFeed(stream_1min=live), notifier)

    asyncio.run(engine.watch(["ZZ"]))

    assert notifier.alerts == []
