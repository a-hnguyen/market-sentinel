"""Fan an alert out to several notifiers (e.g. console + Discord).

Each notifier is sent to independently; one failing (or being slow) does not
stop the others from receiving the alert.
"""

import logging

from ..interfaces import Notifier
from ..models import Alert


class MultiNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = list(notifiers)
        self._log = logging.getLogger("alertengine.notify")

    async def send(self, alert: Alert) -> None:
        for n in self._notifiers:
            try:
                await n.send(alert)
            except Exception as e:  # one channel failing shouldn't block the rest
                self._log.warning("%s failed: %s", type(n).__name__, e)
