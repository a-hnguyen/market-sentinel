import asyncio
from datetime import datetime, timezone

from alertengine.interfaces import Notifier
from alertengine.models import Alert
from alertengine.notifiers.tagged_notifier import TaggedNotifier


class Capture(Notifier):
    def __init__(self):
        self.alert = None

    async def send(self, alert):
        self.alert = alert


def test_tagged_notifier_adds_watcher_without_mutating_original():
    capture = Capture()
    notifier = TaggedNotifier(capture, "penny-overnight")
    original = Alert(
        "AMC",
        datetime(2026, 8, 7, tzinfo=timezone.utc),
        "rule",
        "message",
        {"rsi": 20.0},
        "buy",
    )

    asyncio.run(notifier.send(original))

    assert original.context == {"rsi": 20.0}
    assert capture.alert.context == {"rsi": 20.0, "watcher": "penny-overnight"}
