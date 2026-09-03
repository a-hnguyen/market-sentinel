"""Technical indicators computed from completed bar data.

Functions take oldest-to-newest values and return the latest result. RSI uses
Wilder's smoothing; stochastic uses the standard slow %K and %D averages.
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


def stochastic_oscillator(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    k_period: int = 14,
    k_smoothing: int = 3,
    d_period: int = 3,
) -> tuple[float, float]:
    """Return the latest slow (%K, %D) values."""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length")
    if min(k_period, k_smoothing, d_period) < 1:
        raise ValueError("stochastic periods must be positive")

    warmup = k_period + k_smoothing + d_period - 2
    if len(closes) < warmup:
        raise ValueError(f"need >= {warmup} bars, got {len(closes)}")

    high_values = np.asarray(highs, dtype=float)
    low_values = np.asarray(lows, dtype=float)
    close_values = np.asarray(closes, dtype=float)
    raw_k = []
    for end in range(k_period - 1, len(close_values)):
        start = end - k_period + 1
        highest = float(high_values[start : end + 1].max())
        lowest = float(low_values[start : end + 1].min())
        price_range = highest - lowest
        raw_k.append(
            50.0
            if price_range == 0
            else 100.0 * (float(close_values[end]) - lowest) / price_range
        )

    k_values = np.convolve(
        np.asarray(raw_k), np.ones(k_smoothing) / k_smoothing, mode="valid"
    )
    d_values = np.convolve(k_values, np.ones(d_period) / d_period, mode="valid")
    return float(k_values[-1]), float(d_values[-1])
