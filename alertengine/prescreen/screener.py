"""The pre-screen itself: RSI-only oversold confluence across two timeframes.

A ticker survives only if RSI(14) is below the oversold threshold on BOTH the
slow timeframe (e.g. 4h) and the fast timeframe (e.g. 1h). The slow side has
inertia (a genuinely beaten-down name stays oversold for days); the fast side
re-shuffles quickly. Requiring both keeps the sticky, real setups and drops the
noise. There is deliberately no Bollinger check here — the swing screen is
RSI-only.

`evaluate_confluence` is pure (closes in, verdict out) so it's unit-testable
without a feed; `PreScreener.run` orchestrates the batched historical fetches.
"""

from dataclasses import dataclass
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

    def run(self, watchlist: list[tuple[str, str]]) -> list[ScreenResult]:
        """Scan the watchlist, return survivors most-oversold first."""
        symbols = [sym for sym, _ in watchlist]
        category = {sym: cat for sym, cat in watchlist}
        if not symbols:
            return []

        slow = self.feed.fetch_closes(symbols, self.slow_hours, self.slow_lookback_days)
        fast = self.feed.fetch_closes(symbols, self.fast_hours, self.fast_lookback_days)
        now = datetime.now(timezone.utc)

        results: list[ScreenResult] = []
        for sym in symbols:
            verdict = evaluate_confluence(
                slow.get(sym, []),
                fast.get(sym, []),
                self.rsi_period,
                self.rsi_threshold,
            )
            if verdict is None:
                continue  # not enough history on one side; skip quietly
            oversold, r_slow, r_fast = verdict
            if oversold:
                results.append(
                    ScreenResult(sym, r_slow, r_fast, category.get(sym, ""), now)
                )

        # Most oversold first (lowest combined RSI at the top of the sheet).
        results.sort(key=lambda r: r.rsi_slow + r.rsi_fast)
        return results
