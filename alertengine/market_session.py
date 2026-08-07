"""US-equity session labels derived from market-data timestamps."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
REGULAR_OPEN = time(6, 30)


def in_premarket(timestamp: datetime) -> bool:
    """Return whether a bar belongs to the Pacific-time premarket session.

    Production timestamps are timezone-aware UTC values. Mock/replay callers may
    supply naive values; match the rest of the app by interpreting those as UTC.
    The 06:30 bar itself is regular-session data.
    """
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(PACIFIC).time() < REGULAR_OPEN
