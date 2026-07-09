"""Tunable defaults for the engine.

Everything here is a **generic, publishable placeholder** — safe for a public
repo. Real confirmed values (the private screening criteria, trading window, and
pending strategy params) live in `settings_local.py`, which is git-ignored and
overrides these at import time. Keep it that way: no private strategy in this file.
"""

# Indicators — standard textbook defaults; these drive the public layer-1 rule.
BB_PERIOD = 20
BB_STD = 2
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70  # reserved for later exit logic, unused in v1

# Trading window, local time — permissive placeholder (real hours in settings_local).
WINDOW_START = "00:00"
WINDOW_END = "23:59"

# Screen filters — permissive placeholder bounds (real criteria in settings_local).
PRICE_MIN = 1
PRICE_MAX = 100_000
MIN_MARKET_CAP = 0
LOSER_MIN_VOLUME_RATIO = 1.0
LOSER_MIN_PCT_LOSS = 0.0
SCREEN_MIN_ABS_PCT_CHANGE = 0.0  # min |day % change| to list (0 = no filter)

# De-dup / cooldown: min 2-min bars after a buy alert (and setup must clear)
# before a symbol can re-arm. Prevents re-firing on the same oversold episode.
COOLDOWN_BARS = 5

# Stage-2 confirmation (two-stage state machine):
#   layer-1 setup only *arms* a symbol; a BUY fires on CONFIRM_GREEN_BARS
#   consecutive green 2-min closes. If it doesn't confirm within
#   ARM_TIMEOUT_BARS 2-min bars (10 = 20 min), the symbol resets to scratch.
CONFIRM_GREEN_BARS = 2
ARM_TIMEOUT_BARS = 10

# Local overrides (git-ignored): real confirmed params, applied last so they win.
try:
    from .settings_local import *  # noqa: F401,F403
except ImportError:
    pass
