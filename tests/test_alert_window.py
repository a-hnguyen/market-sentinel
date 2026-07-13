"""Alert-window enforcement around the engine's completed 2-min bar path."""

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest

from alertengine.alert_window import AlertWindow
from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import AlertRule, DataFeed, Notifier
from alertengine.models import Alert, Bar
from alertengine.screeners.mock_screener import MockScreener


class _DummyFeed(DataFeed):
    async def stream_bars(self, symbols) -> AsyncIterator[Bar]:
        return
        yield  # pragma: no cover


class _AlwaysHotRule(AlertRule):
    def __init__(self):
        self.calls = 0

    def evaluate(self, symbol, bars):
        self.calls += 1
        last = bars[-1]
        return Alert(symbol, last.timestamp, "test", "hot")


class _RecordingNotifier(Notifier):
    def __init__(self):
        self.alerts: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


def _bar(timestamp: datetime, green: bool = False) -> Bar:
    open_, close = (100.0, 101.0) if green else (100.0, 99.0)
    return Bar("ZZ", timestamp, open_, max(open_, close), min(open_, close), close, 1)


def _engine(start="07:00", end="08:30"):
    rule = _AlwaysHotRule()
    notifier = _RecordingNotifier()
    engine = AlertEngine(
        screener=MockScreener(),
        feed=_DummyFeed(),
        rule=rule,
        notifier=notifier,
        gate=ApprovalGate(),
        window_start=start,
        window_end=end,
        alert_timezone="America/Los_Angeles",
    )
    return engine, rule, notifier


async def _feed(engine, *bars):
    for bar in bars:
        await engine._on_2min_bar(bar)


def test_rules_only_run_inside_inclusive_pacific_window():
    engine, rule, notifier = _engine()
    # July is PDT (UTC-7): 13:59 is 06:59, 14:00 is 07:00, and 15:30 is 08:30.
    before = datetime(2026, 7, 2, 13, 59, tzinfo=timezone.utc)
    at_start = datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc)
    at_end = datetime(2026, 7, 2, 15, 30, tzinfo=timezone.utc)
    after = datetime(2026, 7, 2, 15, 31, tzinfo=timezone.utc)

    asyncio.run(_feed(engine, _bar(before), _bar(at_start), _bar(at_end), _bar(after)))

    assert rule.calls == 2
    assert len(notifier.alerts) == 1  # first in-window bar arms; second advances it
    status = engine.status()["symbols"]["ZZ"]
    assert status["bars_seen"] == 4
    assert status["history"] == 4
    assert status["phase"] == "waiting"


def test_outside_bar_cancels_an_in_progress_confirmation():
    engine, _, notifier = _engine()
    inside = datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc)
    outside = datetime(2026, 7, 2, 16, 0, tzinfo=timezone.utc)
    next_day = datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc)

    asyncio.run(
        _feed(
            engine,
            _bar(inside),  # WATCH + ARMED
            _bar(outside),  # reset to WAITING
            _bar(next_day, green=True),  # fresh WATCH; does not confirm old setup
        )
    )

    assert [alert.kind for alert in notifier.alerts] == ["watch", "watch"]
    assert engine.status()["symbols"]["ZZ"]["phase"] == "armed"


def test_window_can_cross_midnight_and_accept_naive_local_bars():
    window = AlertWindow.from_strings("22:00", "02:00", "America/Los_Angeles")

    assert window.contains(datetime(2026, 7, 2, 23, 0))
    assert window.contains(datetime(2026, 7, 3, 2, 0))
    assert not window.contains(datetime(2026, 7, 3, 12, 0))


def test_timezone_conversion_accounts_for_daylight_saving_time():
    window = AlertWindow.from_strings("07:00", "08:30", "America/Los_Angeles")

    assert window.contains(datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc))
    assert window.contains(datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc))


def test_equal_endpoints_mean_always_open():
    window = AlertWindow.from_strings("00:00", "00:00", "America/Los_Angeles")

    assert window.contains(datetime(2026, 7, 2, 12, 0))


@pytest.mark.parametrize("value", ["noon", "25:00", "7:00", "07:00:30", "07:00Z"])
def test_invalid_window_setting_fails_at_startup(value):
    with pytest.raises(ValueError, match="must be a valid HH:MM time"):
        _engine(start=value)


def test_invalid_timezone_fails_at_startup():
    with pytest.raises(ValueError, match="valid IANA timezone"):
        AlertWindow.from_strings("07:00", "08:30", "Pacific")
