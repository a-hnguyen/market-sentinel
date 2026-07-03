"""Sanity checks for indicators against known series."""

import numpy as np
import pytest

from alertengine.indicators import bollinger_bands, rsi


def test_bollinger_constant_series_has_zero_width():
    lower, mid, upper = bollinger_bands([10.0] * 20, period=20, num_std=2)
    assert mid == 10.0
    assert lower == 10.0 and upper == 10.0  # zero std -> bands collapse to mid


def test_bollinger_known_values():
    # Closes 1..20; SMA = 10.5, population std of 1..20 ~= 5.7663.
    closes = list(range(1, 21))
    lower, mid, upper = bollinger_bands(closes, period=20, num_std=2)
    assert mid == pytest.approx(10.5)
    expected_std = float(np.std(np.arange(1, 21), ddof=0))
    assert upper == pytest.approx(10.5 + 2 * expected_std)
    assert lower == pytest.approx(10.5 - 2 * expected_std)


def test_bollinger_uses_only_last_period():
    # Leading noise should be ignored; only the last 20 matter.
    closes = [999.0] * 5 + [10.0] * 20
    lower, mid, upper = bollinger_bands(closes, period=20)
    assert (lower, mid, upper) == (10.0, 10.0, 10.0)


def test_bollinger_too_few_raises():
    with pytest.raises(ValueError):
        bollinger_bands([1.0] * 19, period=20)


def test_rsi_all_gains_is_100():
    closes = list(range(1, 20))  # strictly increasing -> no losses
    assert rsi(closes, period=14) == 100.0


def test_rsi_all_losses_is_0():
    closes = list(range(20, 1, -1))  # strictly decreasing -> no gains
    assert rsi(closes, period=14) == pytest.approx(0.0)


def test_rsi_alternating_is_mid_range():
    # Up/down by 1 repeatedly -> roughly equal avg gain/loss -> RSI in mid band.
    # (Wilder's smoothing won't land exactly on 50; it drifts with the final
    # move's parity, so assert a mid-range band rather than a precise value.)
    closes = []
    price = 50.0
    for i in range(40):
        price += 1 if i % 2 == 0 else -1
        closes.append(price)
    assert 40.0 < rsi(closes, period=14) < 60.0


def test_rsi_too_few_raises():
    with pytest.raises(ValueError):
        rsi([1.0] * 14, period=14)
