"""Local-time policy for deciding when completed bars may produce alerts."""

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class AlertWindow:
    """An inclusive daily window in an IANA timezone.

    Equal endpoints represent an always-open window. A start later than the end
    represents a window that crosses midnight, such as 22:00 through 02:00.
    """

    start: time
    end: time
    timezone: ZoneInfo

    @classmethod
    def from_strings(cls, start: str, end: str, timezone_name: str) -> "AlertWindow":
        return cls(
            start=_parse_hhmm(start, "WINDOW_START"),
            end=_parse_hhmm(end, "WINDOW_END"),
            timezone=_parse_timezone(timezone_name),
        )

    def contains(self, timestamp: datetime) -> bool:
        """Return whether ``timestamp`` falls within this window.

        Production bars are timezone-aware. Naive timestamps, used by the mock
        feed, are interpreted as already local to this window's timezone.
        Comparisons use minute precision because the settings use ``HH:MM``.
        """
        local = (
            timestamp
            if timestamp.tzinfo is None
            else timestamp.astimezone(self.timezone)
        )
        local_time = local.time().replace(second=0, microsecond=0, tzinfo=None)

        if self.start == self.end:
            return True
        if self.start < self.end:
            return self.start <= local_time <= self.end
        return local_time >= self.start or local_time <= self.end


def _parse_hhmm(value: str, setting_name: str) -> time:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError(f"{setting_name} must be a valid HH:MM time")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{setting_name} must be a valid HH:MM time") from exc


def _parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (TypeError, ValueError, ZoneInfoNotFoundError, IsADirectoryError) as exc:
        raise ValueError(
            f"ALERT_TIMEZONE must be a valid IANA timezone: {value!r}"
        ) from exc
