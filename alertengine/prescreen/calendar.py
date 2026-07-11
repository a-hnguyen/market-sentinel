"""Trading-day guard for the unattended pre-screen.

A cron/EventBridge schedule of "weekdays" still fires on market holidays
(July 4th, Thanksgiving, …). On those days the market never opened, so the
pre-screen would just re-scan the prior session's bars and write a fresh-dated
`candidates.csv` that is really yesterday's screen — stale, not wrong. This lets
the cron path skip cleanly instead.

Uses Alpaca's market calendar (no new dependency — the pre-screen already needs
Alpaca creds). On any error (network hiccup, missing creds) it **fails open**
(returns True): better to run an extra time on a real trading day than to
silently skip one because the calendar lookup blipped.
"""

from datetime import date
from zoneinfo import ZoneInfo

# The US market calendar is in Eastern time; "today" must be the ET date, not
# the box's local/UTC date, or a late-evening UTC run could ask about tomorrow.
_ET = ZoneInfo("America/New_York")


def today_et() -> date:
    from datetime import datetime

    return datetime.now(_ET).date()


def is_trading_day(day: date | None = None, client=None) -> bool:
    """True if `day` (ET today by default) is a regular market session.

    `client` is an injectable Alpaca TradingClient for tests; in production it
    is built lazily from ALPACA_* env vars.
    """
    day = day or today_et()
    try:
        if client is None:
            import os

            from alpaca.trading.client import TradingClient

            key = os.environ.get("ALPACA_API_KEY")
            secret = os.environ.get("ALPACA_SECRET_KEY")
            if not key or not secret:
                return True  # can't check -> fail open
            client = TradingClient(key, secret, paper=True)

        from alpaca.trading.requests import GetCalendarRequest

        cal = client.get_calendar(GetCalendarRequest(start=day, end=day))
        return any(entry.date == day for entry in cal)
    except Exception:
        return True  # calendar unreachable -> fail open, run anyway
