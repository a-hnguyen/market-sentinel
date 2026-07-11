"""Exit (SELL) state machine: an overbought setup only ARMS the short side; a
SELL fires on two consecutive red 2-min closes, mirroring the buy side's
two-green confirmation, with the same arm timeout and cooldown gate.

Uses a ScriptedExitRule (overbought on chosen bar indices) as the engine's
`exit_rule`, and a NullRule for the buy side so the two machines are exercised
independently. Bars are fed straight into the engine's per-bar handler.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import AlertRule, DataFeed, Notifier
from alertengine.models import Alert, Bar
from alertengine.screeners.mock_screener import MockScreener

BASE = datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc)


class _Rec(Notifier):
    def __init__(self):
        self.alerts: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


class _DummyFeed(DataFeed):
    async def stream_bars(self, symbols) -> AsyncIterator[Bar]:  # never used
        return
        yield  # pragma: no cover


class NullRule(AlertRule):
    """Buy side that never fires, so the long machine stays WAITING."""

    def evaluate(self, symbol, bars):
        return None


class ScriptedExitRule(AlertRule):
    """Returns an overbought exit alert on the fed-bar indices in `hot`,
    regardless of bar values — isolates the state machine from indicator math."""

    def __init__(self, hot):
        self._hot = set(hot)
        self._i = -1

    def evaluate(self, symbol, bars):
        self._i += 1
        if self._i in self._hot:
            last = bars[-1]
            return Alert(
                symbol,
                last.timestamp,
                "bb_rsi_exit",
                "overbought",
                {"close": last.close, "bb_upper": 0.0, "rsi": 0.0},
            )
        return None


def _bar(i, green, symbol="ZZ"):
    o, c = 100.0, (101.0 if green else 99.0)
    return Bar(
        symbol, BASE + timedelta(minutes=2 * i), o, max(o, c), min(o, c), c, 1000.0
    )


def _engine(exit_rule, notifier, rule=None, **kw):
    return AlertEngine(
        screener=MockScreener(),
        feed=_DummyFeed(),
        rule=rule or NullRule(),
        exit_rule=exit_rule,
        notifier=notifier,
        gate=ApprovalGate(),
        **kw,
    )


async def _feed(engine, bars):
    for b in bars:
        await engine._on_2min_bar(b)


def _kinds(notifier):
    return [a.kind for a in notifier.alerts]


def test_arm_then_two_reds_fires_sell():
    # bar0 overbought -> S-WATCH + arm; bar1, bar2 red -> SELL.
    n = _Rec()
    e = _engine(ScriptedExitRule(hot={0}), n)
    asyncio.run(_feed(e, [_bar(0, True), _bar(1, False), _bar(2, False)]))
    assert _kinds(n) == ["sell_watch", "sell"]
    assert n.alerts[0].rule == "bb_rsi_exit"
    assert n.alerts[1].rule == "bb_rsi_sell"
    assert e.status()["symbols"]["ZZ"]["sell_phase"] == "cooldown"


def test_green_breaks_the_red_streak():
    # arm, red(1), green(reset), red(1), red(2) -> SELL only on bar4.
    n = _Rec()
    e = _engine(ScriptedExitRule(hot={0}), n)
    asyncio.run(
        _feed(
            e,
            [
                _bar(0, True),
                _bar(1, False),
                _bar(2, True),
                _bar(3, False),
                _bar(4, False),
            ],
        )
    )
    assert _kinds(n) == ["sell_watch", "sell"]
    assert n.alerts[1].timestamp == BASE + timedelta(minutes=2 * 4)


def test_arm_bar_red_does_not_count():
    # bar0 arms AND is red — that red must NOT count. So bar1 red = 1 (no sell),
    # and only bar2 red = 2 fires it.
    n = _Rec()
    e = _engine(ScriptedExitRule(hot={0}), n)
    asyncio.run(_feed(e, [_bar(0, False), _bar(1, False)]))
    assert _kinds(n) == ["sell_watch"]
    asyncio.run(_feed(e, [_bar(2, False)]))
    assert _kinds(n) == ["sell_watch", "sell"]


def test_sell_timeout_resets_and_drops_history():
    # arm@0, then 3 green bars, no confirmation -> timeout resets to WAITING.
    # Buy machine never armed (NullRule), so history is dropped as before.
    n = _Rec()
    e = _engine(ScriptedExitRule(hot={0}), n, arm_timeout_bars=3)
    asyncio.run(_feed(e, [_bar(0, True), _bar(1, True), _bar(2, True), _bar(3, True)]))
    assert _kinds(n) == ["sell_watch"]  # never confirmed
    st = e.status()["symbols"]["ZZ"]
    assert st["sell_phase"] == "waiting"
    assert st["history"] == 0


def test_sell_cooldown_blocks_reentry_while_still_overbought():
    n = _Rec()
    e = _engine(ScriptedExitRule(hot={0, 3, 4}), n, cooldown_bars=2)
    asyncio.run(_feed(e, [_bar(0, True), _bar(1, False), _bar(2, False)]))  # -> sell
    asyncio.run(_feed(e, [_bar(3, False), _bar(4, False)]))  # still overbought
    assert _kinds(n) == ["sell_watch", "sell"]  # no new arm
    assert e.status()["symbols"]["ZZ"]["sell_phase"] == "cooldown"


def test_buy_and_sell_are_independent_and_share_protected_history():
    # Both machines wired. bar0 arms BOTH (buy hot + exit hot). bars 1-2 green
    # confirm the BUY (long -> cooldown); those greens do NOT confirm the SELL,
    # which stays armed until its timeout at bar4. At that timeout the long
    # machine is still in COOLDOWN (non-idle), so the shared history must be
    # preserved — a timeout on one direction can't blind the other.

    class ScriptedBuy(AlertRule):
        def __init__(self, hot):
            self._hot = set(hot)
            self._i = -1

        def evaluate(self, symbol, bars):
            self._i += 1
            if self._i in self._hot:
                last = bars[-1]
                return Alert(
                    symbol,
                    last.timestamp,
                    "bb_rsi_layer1",
                    "oversold",
                    {"close": last.close, "bb_lower": 0.0, "rsi": 0.0},
                )
            return None

    n = _Rec()
    e = _engine(
        ScriptedExitRule(hot={0}), n, rule=ScriptedBuy(hot={0}), arm_timeout_bars=4
    )
    asyncio.run(
        _feed(
            e,
            [
                _bar(0, True),  # arms BOTH long and short
                _bar(1, True),  # long green 1
                _bar(2, True),  # long green 2 -> BUY, long cooldown
                _bar(3, True),  # short still armed (greens aren't red)
                _bar(4, True),  # short bars_since_arm=4 -> timeout
            ],
        )
    )
    # A sell was never confirmed; a buy was. Both watches + the buy fired.
    assert _kinds(n) == ["watch", "sell_watch", "buy"]
    st = e.status()["symbols"]["ZZ"]
    assert st["sell_phase"] == "waiting"  # short timed out
    # History preserved: the long machine was still non-idle (cooldown) when the
    # short timed out, so the guard skipped the clear.
    assert st["history"] > 0
