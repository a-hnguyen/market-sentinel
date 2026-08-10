"""1-min -> 2-min bar aggregation.

Alpaca's real-time stream delivers 1-min bars; there is no native 2-min stream,
so we fold consecutive 1-min bars into clock-aligned 2-min bars. A bug here
silently corrupts every indicator downstream, so this module has a dedicated
test (tests/test_aggregator.py) that must pass before anything is built on top.

Design:
- 2-min buckets are aligned to the clock, not to arrival order: each 1-min bar's
  timestamp is floored to an even minute (09:30/09:31 -> 09:30; 09:32/09:33 ->
  09:32). So which bars pair up is decided by their timestamps, not by counting.
- flush-on-advance: a bucket is emitted when a 1-min bar for a *later* bucket
  arrives, so we never wait indefinitely for a second bar that may not come.
- Missing-minute quirk (real on IEX): a quiet minute may produce no 1-min bar,
  leaving a bucket with a single bar. We still emit it rather than stall.
- Historical gap fills: provider-marked interpolated minutes preserve clock
  alignment, but real minutes exclusively determine a mixed bucket's OHLCV. A
  bucket is marked interpolated only when it contains no real trades at all.
- State is per-symbol; symbols are independent.

Assumes bars arrive in non-decreasing timestamp order per symbol (as a real feed
delivers them).
"""

from dataclasses import dataclass
from datetime import datetime

from .models import Bar


def bucket_start(ts: datetime) -> datetime:
    """Floor a timestamp to its even-minute 2-min bucket start."""
    return ts.replace(minute=(ts.minute // 2) * 2, second=0, microsecond=0)


@dataclass
class _Bucket:
    """Accumulating OHLCV for one symbol's in-progress 2-min bar."""

    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    has_real_trade: bool
    session: str | None

    def merge(self, bar: Bar) -> None:
        if not bar.interpolated and not self.has_real_trade:
            # A provider may emit a carry-forward minute before the first real
            # trade in this bucket. Do not let that synthetic price fabricate
            # the two-minute candle's open or range.
            self.open = bar.open
            self.high = bar.high
            self.low = bar.low
            self.close = bar.close
            self.volume = bar.volume
            self.has_real_trade = True
            self.session = bar.session or self.session
            return

        if bar.interpolated and self.has_real_trade:
            # Gap fills carry no new market information. Once a bucket has a
            # real trade, its OHLCV must be derived from real minutes only.
            return

        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close  # last 1-min close in the bucket
        self.volume += bar.volume
        self.has_real_trade = self.has_real_trade or not bar.interpolated
        self.session = bar.session or self.session

    def to_bar(self) -> Bar:
        return Bar(
            symbol=self.symbol,
            timestamp=self.start,  # bar START time
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            interpolated=not self.has_real_trade,
            session=self.session,
        )


class BarAggregator:
    """Folds 1-min bars into 2-min bars, per symbol."""

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}

    def add(self, bar: Bar) -> Bar | None:
        """Feed one 1-min bar. Returns a completed 2-min Bar if this bar
        advanced to a new bucket (flushing the previous one), else None.
        """
        start = bucket_start(bar.timestamp)
        current = self._buckets.get(bar.symbol)

        if current is not None and start == current.start:
            current.merge(bar)
            return None

        # New bucket (either the first bar for this symbol, or a later bucket).
        emitted = current.to_bar() if current is not None else None
        self._buckets[bar.symbol] = _Bucket(
            symbol=bar.symbol,
            start=start,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            has_real_trade=not bar.interpolated,
            session=bar.session,
        )
        return emitted

    def flush(self, symbol: str) -> Bar | None:
        """Emit and clear the in-progress bucket for one symbol, if any."""
        current = self._buckets.pop(symbol, None)
        return current.to_bar() if current is not None else None

    def flush_all(self) -> list[Bar]:
        """Emit and clear every in-progress bucket (e.g. at end of session)."""
        bars = [b.to_bar() for b in self._buckets.values()]
        self._buckets.clear()
        return bars
