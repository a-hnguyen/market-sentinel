from datetime import datetime, timezone
from typing import AsyncIterator

import pytest

from alertengine import settings
from alertengine.__main__ import _strategy_registry
from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import AlertRule, DataFeed, Notifier
from alertengine.models import Alert, Bar
from alertengine.screeners.mock_screener import MockScreener
from alertengine.strategy import StrategyConfig


class _Rule(AlertRule):
    def evaluate(self, symbol, bars):
        return None


class _Feed(DataFeed):
    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        return
        yield  # pragma: no cover


class _Notifier(Notifier):
    async def send(self, alert: Alert) -> None:
        pass


def _strategy(name, minutes, timeout):
    return StrategyConfig(
        name=name,
        rule=_Rule(),
        bar_interval_minutes=minutes,
        arm_timeout_bars=timeout,
    )


def _engine():
    strategies = {
        "slow": _strategy("slow", 2, 15),
        "fast": _strategy("fast", 1, 15),
    }
    return AlertEngine(
        screener=MockScreener(),
        feed=_Feed(),
        rule=strategies["slow"].rule,
        notifier=_Notifier(),
        gate=ApprovalGate(),
        strategies=strategies,
        strategy_name="slow",
    )


def test_select_strategy_updates_runtime_and_clears_incompatible_history():
    engine = _engine()
    state = engine._states.setdefault("ZZ", engine._new_state())
    state.history.append(Bar("ZZ", datetime.now(timezone.utc), 1, 1, 1, 1, 1))

    changed = engine.select_strategy(" FAST ")

    assert changed is True
    assert engine.strategy_name == "fast"
    assert engine.bar_interval_minutes == 1
    assert engine.arm_timeout_bars == 15
    assert engine._states == {}
    assert engine.status()["available_strategies"] == ["fast", "slow"]


def test_selecting_active_strategy_is_idempotent():
    engine = _engine()
    state = engine._states.setdefault("ZZ", engine._new_state())

    assert engine.select_strategy("slow") is False
    assert engine._states["ZZ"] is state


def test_select_strategy_rejects_unknown_name():
    with pytest.raises(ValueError, match="available: fast, slow"):
        _engine().select_strategy("missing")


def test_registry_key_must_match_strategy_name():
    strategy = _strategy("one", 1, 15)

    with pytest.raises(ValueError, match="keys must match"):
        AlertEngine(
            screener=MockScreener(),
            feed=_Feed(),
            rule=strategy.rule,
            notifier=_Notifier(),
            gate=ApprovalGate(),
            strategies={"other": strategy},
            strategy_name="other",
        )


def test_strategy_registry_supports_legacy_private_settings(monkeypatch):
    monkeypatch.delattr(settings, "STRATEGIES", raising=False)
    monkeypatch.delattr(settings, "ACTIVE_STRATEGY", raising=False)
    monkeypatch.setattr(settings, "BUY_SETUP_RULE", _Rule(), raising=False)
    monkeypatch.setattr(settings, "BAR_INTERVAL_MINUTES", 1, raising=False)

    strategies, active = _strategy_registry()

    assert active == "private"
    assert strategies[active].bar_interval_minutes == 1
