"""Integration: the state machine fans every remote-visible stage to console
and chat. Discord replaces the local REPL, so both the arm and confirmation are
useful remotely.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import AlertRule, DataFeed, Notifier
from alertengine.models import Alert, Bar
from alertengine.notifiers.console_notifier import ConsoleNotifier
from alertengine.notifiers.multi_notifier import MultiNotifier
from alertengine.screeners.mock_screener import MockScreener

BASE = datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc)


class _DummyFeed(DataFeed):
    async def stream_bars(self, symbols) -> AsyncIterator[Bar]:
        return
        yield  # pragma: no cover


class _ArmOnceRule(AlertRule):
    """Oversold on the first bar only (arms), None after."""

    def __init__(self):
        self._first = True

    def evaluate(self, symbol, bars):
        if self._first:
            self._first = False
            last = bars[-1]
            return Alert(
                symbol,
                last.timestamp,
                "bb_rsi_layer1",
                "oversold",
                {"close": last.close, "bb_lower": 0.0, "rsi": 0.0},
            )
        return None


def _bar(i, green):
    o, c = 100.0, (101.0 if green else 99.0)
    return Bar(
        "ZZ", BASE + timedelta(minutes=2 * i), o, max(o, c), min(o, c), c, 1000.0
    )


class _Chat(Notifier):
    def __init__(self):
        self.alerts = []

    async def send(self, alert):
        self.alerts.append(alert)


def test_watch_and_buy_go_to_console_and_chat(tmp_path, capsys):
    console = ConsoleNotifier(logfile=str(tmp_path / "alerts.log"))
    chat = _Chat()
    engine = AlertEngine(
        screener=MockScreener(),
        feed=_DummyFeed(),
        rule=_ArmOnceRule(),
        notifier=MultiNotifier([console, chat]),
        gate=ApprovalGate(),
    )

    async def drive():
        await engine._on_2min_bar(_bar(0, green=False))  # arm -> WATCH
        await engine._on_2min_bar(_bar(1, green=True))  # green 1
        await engine._on_2min_bar(_bar(2, green=True))  # green 2 -> BUY

    asyncio.run(drive())

    out = capsys.readouterr().out
    assert "[WATCH" in out
    assert "[BUY" in out
    assert [a.kind for a in chat.alerts] == ["watch", "buy"]


def test_multi_notifier_isolates_a_failed_channel():
    class _Broken(Notifier):
        async def send(self, alert):
            raise RuntimeError("offline")

    first, second = _Chat(), _Chat()
    alert = Alert("AAPL", BASE, "test", "hello")
    asyncio.run(MultiNotifier([first, _Broken(), second]).send(alert))
    assert first.alerts == [alert]
    assert second.alerts == [alert]
