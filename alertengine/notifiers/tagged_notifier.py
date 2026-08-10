"""Attach watcher provenance before forwarding an alert."""

from dataclasses import replace

from ..interfaces import Notifier
from ..models import Alert


class TaggedNotifier(Notifier):
    def __init__(self, notifier: Notifier, label: str) -> None:
        self._notifier = notifier
        self._label = label

    async def send(self, alert: Alert) -> None:
        context = dict(alert.context or {})
        context["watcher"] = self._label
        await self._notifier.send(replace(alert, context=context))
