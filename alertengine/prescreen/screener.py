"""RSI-only overbought/oversold confluence across two timeframes.

A ticker survives when RSI(14) is either below the oversold threshold on both
timeframes or above the overbought threshold on both. There is deliberately no
Bollinger check here; BB remains part of the live two-minute setup rules.

`evaluate_confluence` is pure (closes in, verdict out) so it's unit-testable
without a feed; `PreScreener.run` orchestrates the batched historical fetches.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import settings
from ..indicators import rsi


@dataclass
class ScreenResult:
    symbol: str
    rsi_slow: float  # RSI on the slow timeframe (e.g. 4h)
    rsi_fast: float  # RSI on the fast timeframe (e.g. 1h)
    category: str  # the watchlist "List" label this ticker came from
    scanned_at: datetime
    signal: str = "oversold"


@dataclass
class PreScreenReport:
    """Observable output of both timeframes for both signal directions."""

    oversold_slow_matches: list[str] = field(default_factory=list)
    oversold_fast_matches: list[str] = field(default_factory=list)
    oversold_results: list[ScreenResult] = field(default_factory=list)
    overbought_slow_matches: list[str] = field(default_factory=list)
    overbought_fast_matches: list[str] = field(default_factory=list)
    overbought_results: list[ScreenResult] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def results(self) -> list[ScreenResult]:
        """Combined automatic watchlist, with direction retained per row."""
        return [*self.oversold_results, *self.overbought_results]


def evaluate_confluence(
    slow_closes: list[float],
    fast_closes: list[float],
    rsi_period: int,
    threshold: float,
) -> tuple[bool, float, float] | None:
    """(oversold_on_both, rsi_slow, rsi_fast), or None if either series lacks
    enough bars to compute RSI. Needs > rsi_period closes per side."""
    if len(slow_closes) <= rsi_period or len(fast_closes) <= rsi_period:
        return None
    r_slow = rsi(slow_closes, rsi_period)
    r_fast = rsi(fast_closes, rsi_period)
    return (r_slow < threshold and r_fast < threshold), r_slow, r_fast


class PreScreener:
    def __init__(
        self,
        feed,
        slow_hours: int = settings.PRESCREEN_SLOW_HOURS,
        slow_lookback_days: int = settings.PRESCREEN_SLOW_LOOKBACK_DAYS,
        fast_hours: int = settings.PRESCREEN_FAST_HOURS,
        fast_lookback_days: int = settings.PRESCREEN_FAST_LOOKBACK_DAYS,
        rsi_period: int = settings.RSI_PERIOD,
        rsi_threshold: float = settings.PRESCREEN_RSI_THRESHOLD,
        rsi_overbought_threshold: float = settings.PRESCREEN_RSI_OVERBOUGHT,
    ) -> None:
        # feed is anything with fetch_closes(symbols, hours, lookback_days)
        # -> {SYMBOL: [closes]} (AlpacaFeed in production, a fake in tests).
        self.feed = feed
        self.slow_hours = slow_hours
        self.slow_lookback_days = slow_lookback_days
        self.fast_hours = fast_hours
        self.fast_lookback_days = fast_lookback_days
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold
        self.rsi_overbought_threshold = rsi_overbought_threshold

    def run_report(self, watchlist: list[tuple[str, str]]) -> PreScreenReport:
        """Expose each RSI leg and intersection for both directions."""
        symbols = [sym for sym, _ in watchlist]
        category = {sym: cat for sym, cat in watchlist}
        if not symbols:
            return PreScreenReport()

        slow = self.feed.fetch_closes(
            symbols,
            self.slow_hours,
            self.slow_lookback_days,
            regular_session=True,
        )
        fast = self.feed.fetch_closes(
            symbols,
            self.fast_hours,
            self.fast_lookback_days,
            regular_session=True,
        )
        now = datetime.now(timezone.utc)

        report = PreScreenReport()
        for sym in symbols:
            slow_closes = slow.get(sym, [])
            fast_closes = fast.get(sym, [])
            if (
                len(slow_closes) <= self.rsi_period
                or len(fast_closes) <= self.rsi_period
            ):
                continue  # not enough history on one side; skip quietly
            r_slow = rsi(slow_closes, self.rsi_period)
            r_fast = rsi(fast_closes, self.rsi_period)
            if r_slow < self.rsi_threshold:
                report.oversold_slow_matches.append(sym)
            if r_fast < self.rsi_threshold:
                report.oversold_fast_matches.append(sym)
            if r_slow > self.rsi_overbought_threshold:
                report.overbought_slow_matches.append(sym)
            if r_fast > self.rsi_overbought_threshold:
                report.overbought_fast_matches.append(sym)
            if r_slow < self.rsi_threshold and r_fast < self.rsi_threshold:
                report.oversold_results.append(
                    ScreenResult(
                        sym, r_slow, r_fast, category.get(sym, ""), now, "oversold"
                    )
                )
            elif (
                r_slow > self.rsi_overbought_threshold
                and r_fast > self.rsi_overbought_threshold
            ):
                report.overbought_results.append(
                    ScreenResult(
                        sym, r_slow, r_fast, category.get(sym, ""), now, "overbought"
                    )
                )

        report.oversold_results.sort(key=lambda r: r.rsi_slow + r.rsi_fast)
        report.overbought_results.sort(
            key=lambda r: r.rsi_slow + r.rsi_fast, reverse=True
        )
        return report

    def run(self, watchlist: list[tuple[str, str]]) -> list[ScreenResult]:
        """Return the union of final oversold and overbought intersections."""
        return self.run_report(watchlist).results
