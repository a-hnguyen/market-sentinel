"""Bollinger Bands and RSI, computed on a 2-min close series.

Both functions take a sequence of closes (oldest -> newest) and return the value
for the *latest* bar. Standard formulas; RSI uses Wilder's smoothing.
"""

from collections.abc import Sequence

import numpy as np


def bollinger_bands(
    closes: Sequence[float], period: int = 20, num_std: float = 2
) -> tuple[float, float, float]:
    """Return (lower, mid, upper) for the latest bar.

    mid is the SMA over the last `period` closes; the bands are `num_std`
    population standard deviations away. Requires at least `period` closes.
    """
    if len(closes) < period:
        raise ValueError(f"need >= {period} closes, got {len(closes)}")
    window = np.asarray(closes[-period:], dtype=float)
    mid = float(window.mean())
    std = float(window.std(ddof=0))  # population std, standard for Bollinger
    lower = mid - num_std * std
    upper = mid + num_std * std
    return lower, mid, upper


def rsi(closes: Sequence[float], period: int = 14) -> float:
    """Return the latest RSI value using Wilder's smoothing.

    Requires at least `period + 1` closes (one extra for the first delta).
    """
    if len(closes) < period + 1:
        raise ValueError(f"need >= {period + 1} closes, got {len(closes)}")

    prices = np.asarray(closes, dtype=float)
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Seed with the simple average of the first `period` gains/losses...
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    # ...then apply Wilder's smoothing across the remaining deltas.
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0  # no losses over the window -> fully overbought
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))
