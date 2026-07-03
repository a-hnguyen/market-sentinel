"""End-to-end: mock screen -> approve -> watch -> alert, no network, no keys.

Uses asyncio.run rather than a pytest-asyncio marker so it runs with plain
pytest (no extra plugin).
"""

import asyncio

from alertengine.engine import AlertEngine
from alertengine.feeds.mock_feed import MockFeed
from alertengine.gate import ApprovalGate
from alertengine.interfaces import Notifier
from alertengine.models import Alert
from alertengine.rules.bb_rsi_rule import BBRSIRule
from alertengine.screeners.mock_screener import MockScreener


class CapturingNotifier(Notifier):
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


async def _run_full_pipeline():
    gate = ApprovalGate()
    notifier = CapturingNotifier()
    engine = AlertEngine(
        screener=MockScreener(),
        feed=MockFeed(symbols=["MOCK"], interval=0.0),
        rule=BBRSIRule(),
        notifier=notifier,
        gate=gate,
    )

    candidates = await engine.screen()
    assert any(c.symbol == "MOCK" for c in candidates)

    gate.approve("MOCK")
    assert gate.watchlist() == ["MOCK"]

    await engine.watch(gate.watchlist())

    # The default mock path is shaped to trigger the layer-1 setup.
    assert len(notifier.alerts) >= 1
    a = notifier.alerts[0]
    assert a.symbol == "MOCK"
    assert a.rule == "bb_rsi_layer1"
    assert a.context["close"] < a.context["bb_lower"]
    assert a.context["rsi"] < 30


def test_full_pipeline_fires_alert():
    asyncio.run(_run_full_pipeline())


async def _run_dedup():
    """Even if the setup holds for many bars, alerts are de-duped/cooled down."""
    notifier = CapturingNotifier()
    engine = AlertEngine(
        screener=MockScreener(),
        feed=MockFeed(symbols=["MOCK"], interval=0.0),
        rule=BBRSIRule(),
        notifier=notifier,
        gate=ApprovalGate(),
        cooldown_bars=5,
    )
    await engine.watch(["MOCK"])
    # Far fewer alerts than 2-min bars seen -> not firing every bar.
    seen = engine.status()["symbols"]["MOCK"]["bars_seen"]
    assert len(notifier.alerts) < seen


def test_dedup_does_not_spam_every_bar():
    asyncio.run(_run_dedup())
