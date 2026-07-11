"""Notifier that prints alerts to the console and appends them to a log file.

The log file is git-ignored (*.log). Swapping in SMS/email/Slack later is just
another Notifier implementation behind the same interface.
"""

import logging
from datetime import timezone
from zoneinfo import ZoneInfo

from ..interfaces import Notifier
from ..models import Alert

# Display alert times in US Pacific. ZoneInfo handles PST/PDT automatically, so
# summer alerts correctly show PDT and winter ones PST.
_PACIFIC = ZoneInfo("America/Los_Angeles")


class ConsoleNotifier(Notifier):
    def __init__(self, logfile: str = "alerts.log") -> None:
        self._log = logging.getLogger("alertengine.alerts")
        self._log.setLevel(logging.INFO)
        # Avoid duplicate handlers if constructed more than once.
        if not any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "").endswith(logfile)
            for h in self._log.handlers
        ):
            handler = logging.FileHandler(logfile)
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            self._log.addHandler(handler)

    async def send(self, alert: Alert) -> None:
        # Alpaca bar timestamps are UTC (tz-aware); mock bars are naive — treat
        # those as UTC too, then convert to Pacific for display.
        ts = alert.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        local = ts.astimezone(_PACIFIC)

        # Build aligned columns from the alert's numeric context when present,
        # so alerts line up regardless of price magnitude; fall back to the
        # rule's free-form message for rules that don't populate these keys.
        c = alert.context or {}
        if {"close", "rsi"} <= c.keys() and ("bb_lower" in c or "bb_upper" in c):
            # Buy alerts carry the lower band, sell alerts the upper — show whichever.
            if "bb_lower" in c:
                band = f"lower BB {c['bb_lower']:>9.2f}"
            else:
                band = f"upper BB {c['bb_upper']:>9.2f}"
            body = f"close {c['close']:>9.2f}   {band}   RSI {c['rsi']:>5.1f}"
            # Day % change / relative volume come from screening; show when known.
            if "pct_change" in c:
                body += f"   chg {c['pct_change']:>+6.1f}%"
            if "volume_ratio" in c:
                body += f"   volx {c['volume_ratio']:>4.1f}"
        else:
            body = alert.message
        # Label the stages distinctly so the console reads clearly: WATCH/S-WCH =
        # armed setup (buy/sell side), BUY/SELL = two-close confirmation.
        tag = {
            "watch": "WATCH",
            "buy": "BUY  ",
            "sell_watch": "S-WCH",
            "sell": "SELL ",
        }.get(alert.kind, "ALERT")
        # Date included because replay bars span multiple days; %Z -> PST/PDT.
        line = f"[{tag} {local:%Y-%m-%d %H:%M %Z}]  {alert.symbol:<6}  {body}"
        print(line)
        self._log.info("%s %s %s", alert.symbol, alert.rule, alert.context)
