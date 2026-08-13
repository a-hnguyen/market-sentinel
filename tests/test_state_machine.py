"""Stage-2 state machine: layer-1 only ARMS a symbol; a BUY fires on two
consecutive green 2-min closes, with a 20-min arm timeout and a cooldown gate.

A ScriptedRule reports "oversold" on chosen bar indices so these tests exercise
the state transitions directly, independent of the BB/RSI math (covered
elsewhere). Bars are fed straight into the engine's per-bar handler.

asyncio.run rather than a pytest-asyncio marker, to avoid the plugin.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import AlertRule, ConfirmationRule, DataFeed, Notifier
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


class ScriptedRule(AlertRule):
    """Returns a layer-1 alert (oversold) on the fed-bar indices in `hot`,
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
                "bb_rsi_layer1",
                "oversold",
                {"close": last.close, "bb_lower": 0.0, "rsi": 0.0},
            )
        return None


class ScriptedConfirmationRule(ConfirmationRule):
    def __init__(self, results):
        self._results = iter(results)
        self.calls = 0

    def evaluate(self, symbol, bars):
        self.calls += 1
        return next(self._results)


def _bar(i, green, symbol="ZZ", *, interpolated=False):
    o, c = 100.0, (101.0 if green else 99.0)
    return Bar(
        symbol,
        BASE + timedelta(minutes=2 * i),
        o,
        max(o, c),
        min(o, c),
        c,
        1000.0,
        interpolated=interpolated,
    )


def _engine(rule, notifier, **kw):
    return AlertEngine(
        screener=MockScreener(),
        feed=_DummyFeed(),
        rule=rule,
        notifier=notifier,
        gate=ApprovalGate(),
        **kw,
    )


async def _feed(engine, bars):
    for b in bars:
        await engine._on_2min_bar(b)


def _kinds(notifier):
    return [a.kind for a in notifier.alerts]


def test_arm_then_two_greens_fires_buy():
    # bar0 oversold -> WATCH + arm; bar1, bar2 green -> BUY.
    n = _Rec()
    e = _engine(ScriptedRule(hot={0}), n)
    asyncio.run(_feed(e, [_bar(0, False), _bar(1, True), _bar(2, True)]))
    assert _kinds(n) == ["watch", "buy"]
    assert n.alerts[0].rule == "bb_rsi_layer1"
    assert n.alerts[1].rule == "bb_rsi_buy"
    assert e.status()["symbols"]["ZZ"]["phase"] == "cooldown"


def test_optional_confirmation_rule_can_delay_buy_and_enrich_alert():
    # The bar-pattern confirmation is ready at bar2, but the optional strategy
    # gate blocks once. A third consecutive green bar rechecks and then fires.
    n = _Rec()
    confirmation = ScriptedConfirmationRule([None, {"confirmation_metric": 1.25}])
    e = _engine(ScriptedRule(hot={0}), n, buy_confirmation_rule=confirmation)

    asyncio.run(_feed(e, [_bar(0, False), _bar(1, True), _bar(2, True), _bar(3, True)]))

    assert _kinds(n) == ["watch", "buy"]
    assert confirmation.calls == 2
    assert n.alerts[-1].timestamp == BASE + timedelta(minutes=6)
    assert n.alerts[-1].context["confirmation_metric"] == 1.25


def test_confirmation_rule_cannot_extend_the_arm_timeout():
    n = _Rec()
    confirmation = ScriptedConfirmationRule([None, None])
    e = _engine(
        ScriptedRule(hot={0}),
        n,
        buy_confirmation_rule=confirmation,
        arm_timeout_bars=3,
    )

    asyncio.run(_feed(e, [_bar(0, False), _bar(1, True), _bar(2, True), _bar(3, True)]))

    assert _kinds(n) == ["watch"]
    assert confirmation.calls == 2
    status = e.status()["symbols"]["ZZ"]
    assert status["phase"] == "waiting"
    assert status["history"] == 0


def test_red_breaks_the_green_streak():
    # arm, green(1), red(reset), green(1), green(2) -> BUY only on bar4.
    n = _Rec()
    e = _engine(ScriptedRule(hot={0}), n)
    asyncio.run(
        _feed(
            e,
            [
                _bar(0, False),
                _bar(1, True),
                _bar(2, False),
                _bar(3, True),
                _bar(4, True),
            ],
        )
    )
    assert _kinds(n) == ["watch", "buy"]
    assert n.alerts[1].timestamp == BASE + timedelta(minutes=2 * 4)


def test_interpolated_green_body_cannot_confirm_buy():
    n = _Rec()
    e = _engine(ScriptedRule(hot={0}), n)
    asyncio.run(
        _feed(
            e,
            [
                _bar(0, False),
                _bar(1, True),
                _bar(2, True, interpolated=True),
                _bar(3, True),
                _bar(4, True),
            ],
        )
    )

    assert _kinds(n) == ["watch", "buy"]
    assert n.alerts[-1].timestamp == BASE + timedelta(minutes=8)


def test_arm_bar_green_does_not_count():
    # bar0 arms AND is green — that green must NOT count. So bar1 green = 1 (no
    # buy), and only bar2 green = 2 fires it.
    n = _Rec()
    e = _engine(ScriptedRule(hot={0}), n)
    asyncio.run(_feed(e, [_bar(0, True), _bar(1, True)]))
    assert _kinds(n) == ["watch"]
    asyncio.run(_feed(e, [_bar(2, True)]))
    assert _kinds(n) == ["watch", "buy"]


def test_timeout_resets_symbol_and_drops_history():
    # arm@0, then 3 red bars, no confirmation -> timeout resets to WAITING and
    # clears bar history.
    n = _Rec()
    e = _engine(ScriptedRule(hot={0}), n, arm_timeout_bars=3)
    asyncio.run(
        _feed(e, [_bar(0, False), _bar(1, False), _bar(2, False), _bar(3, False)])
    )
    assert _kinds(n) == ["watch"]  # never confirmed
    st = e.status()["symbols"]["ZZ"]
    assert st["phase"] == "waiting"
    assert st["history"] == 0


def test_cooldown_blocks_reentry_while_still_oversold():
    # After a buy, the setup stays oversold — must NOT re-arm on the same episode.
    n = _Rec()
    e = _engine(ScriptedRule(hot={0, 3, 4}), n, cooldown_bars=2)
    asyncio.run(_feed(e, [_bar(0, False), _bar(1, True), _bar(2, True)]))  # -> buy
    asyncio.run(_feed(e, [_bar(3, True), _bar(4, True)]))  # still oversold
    assert _kinds(n) == ["watch", "buy"]  # no new watch
    assert e.status()["symbols"]["ZZ"]["phase"] == "cooldown"


def test_cooldown_rearms_after_setup_clears_and_floor():
    # buy -> cooldown; setup clears; after the min floor it returns to WAITING,
    # and a fresh oversold bar re-arms (a new WATCH).
    n = _Rec()
    e = _engine(ScriptedRule(hot={0, 5}), n, cooldown_bars=2)
    asyncio.run(
        _feed(
            e,
            [
                _bar(0, False),
                _bar(1, True),
                _bar(2, True),  # -> buy, cooldown
                _bar(3, False),  # cooldown: floor 1/2, cleared
                _bar(4, False),  # cooldown: floor 2/2 -> WAITING
                _bar(5, False),  # WAITING + oversold -> re-arm
            ],
        )
    )
    assert _kinds(n) == ["watch", "buy", "watch"]
    assert e.status()["symbols"]["ZZ"]["phase"] == "armed"


def test_daily_delivery_limit_suppresses_repeat_kinds_until_next_pacific_day():
    n = _Rec()
    e = _engine(
        ScriptedRule(hot={0, 5, 10}),
        n,
        cooldown_bars=2,
        notify_once_per_kind_per_day=True,
    )
    bars = [
        _bar(0, False),  # first WATCH
        _bar(1, True),
        _bar(2, True),  # first BUY
        _bar(3, False),
        _bar(4, False),  # cooldown clears
        _bar(5, False),  # second WATCH is suppressed
        _bar(6, True),
        _bar(7, True),  # second BUY is suppressed
    ]
    asyncio.run(_feed(e, bars))

    assert _kinds(n) == ["watch", "buy"]
    # Suppression affects delivery only: the second setup still reached
    # COOLDOWN, proving that the state machine kept processing it.
    assert e.status()["symbols"]["ZZ"]["phase"] == "cooldown"

    # Clear cooldown on the next Pacific day, then both kinds may deliver again.
    next_day = BASE + timedelta(days=1)
    next_bars = [
        Bar("ZZ", next_day, 100, 100, 99, 99, 1000),
        Bar("ZZ", next_day + timedelta(minutes=2), 100, 100, 99, 99, 1000),
        Bar("ZZ", next_day + timedelta(minutes=4), 100, 100, 99, 99, 1000),
        Bar("ZZ", next_day + timedelta(minutes=6), 100, 101, 100, 101, 1000),
        Bar("ZZ", next_day + timedelta(minutes=8), 100, 101, 100, 101, 1000),
    ]
    asyncio.run(_feed(e, next_bars))

    assert _kinds(n) == ["watch", "buy", "watch", "buy"]
