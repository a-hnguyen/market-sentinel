"""Integration: the two-stage machine + MultiNotifier route each stage to the
right channel. WATCH (armed setup) is console-only; BUY (two-green confirm) goes
to console AND phone. This wires the real ConsoleNotifier and real NtfyNotifier
(network mocked) behind a MultiNotifier, exactly as `__main__` does in live mode.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import AlertRule, DataFeed
from alertengine.models import Alert, Bar
from alertengine.notifiers.console_notifier import ConsoleNotifier
from alertengine.notifiers.multi_notifier import MultiNotifier
from alertengine.notifiers.ntfy_notifier import NtfyNotifier
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


def test_watch_console_only_buy_goes_to_both(tmp_path, capsys, monkeypatch):
    posts = {"n": 0}

    def fake_urlopen(req, timeout=None):
        posts["n"] += 1

        class _Resp:
            pass

        return _Resp()

    monkeypatch.setattr(
        "alertengine.notifiers.ntfy_notifier.urllib.request.urlopen", fake_urlopen
    )

    console = ConsoleNotifier(logfile=str(tmp_path / "alerts.log"))
    phone = NtfyNotifier(topic="t")
    engine = AlertEngine(
        screener=MockScreener(),
        feed=_DummyFeed(),
        rule=_ArmOnceRule(),
        notifier=MultiNotifier([console, phone]),
        gate=ApprovalGate(),
    )

    async def drive():
        await engine._on_2min_bar(_bar(0, green=False))  # arm -> WATCH
        await engine._on_2min_bar(_bar(1, green=True))  # green 1
        await engine._on_2min_bar(_bar(2, green=True))  # green 2 -> BUY

    asyncio.run(drive())

    out = capsys.readouterr().out
    # Console saw both stages...
    assert "[WATCH" in out
    assert "[BUY" in out
    # ...but the phone was hit exactly once — for the BUY, not the WATCH.
    assert posts["n"] == 1
