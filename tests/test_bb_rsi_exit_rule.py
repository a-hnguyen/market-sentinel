"""Exit rule (public textbook mirror): close > upper BB AND RSI > overbought.

The mirror of the buy rule, so it's tested the same way — direct evaluation over
crafted close series, independent of the state machine.
"""

from datetime import datetime, timedelta, timezone

from alertengine.models import Bar
from alertengine.rules.bb_rsi_exit_rule import BBRSIExitRule

BASE = datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc)


def _bars(closes):
    # OHLC don't matter to the rule (it reads closes only); keep them simple.
    return [
        Bar("ZZ", BASE + timedelta(minutes=2 * i), c, c, c, c, 1000.0)
        for i, c in enumerate(closes)
    ]


def test_fires_when_overbought():
    # Flat base then a jump: the last close pops above a tight upper band, and
    # the lone up-move with no losses drives RSI to ~100 (> 70).
    rule = BBRSIExitRule()
    alert = rule.evaluate("ZZ", _bars([10.0] * 19 + [12.0]))
    assert alert is not None
    assert alert.rule == "bb_rsi_exit"
    assert alert.kind == "alert"  # engine relabels to sell_watch on arm
    assert set(alert.context) >= {"close", "bb_upper", "rsi"}
    assert alert.context["close"] > alert.context["bb_upper"]
    assert alert.context["rsi"] > 70


def test_no_fire_when_not_overbought():
    # A falling series: close sits below the upper band and RSI is low.
    rule = BBRSIExitRule()
    assert rule.evaluate("ZZ", _bars([100.0 - i for i in range(24)])) is None


def test_no_fire_on_flat_series():
    # Constant closes collapse the bands (upper == close), so close is not
    # strictly greater — no false positive.
    rule = BBRSIExitRule()
    assert rule.evaluate("ZZ", _bars([10.0] * 24)) is None


def test_none_when_insufficient_history():
    rule = BBRSIExitRule()
    assert rule.evaluate("ZZ", _bars([10.0] * 5)) is None
