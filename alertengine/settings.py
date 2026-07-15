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
RSI_OVERBOUGHT = 70  # used by the public textbook SELL setup

# Alert window, Pacific time — permissive placeholder (real hours in
# settings_local). Bars outside this window still warm indicator history, but
# cannot arm or advance an alert.
ALERT_TIMEZONE = "America/Los_Angeles"
WINDOW_START = "00:00"
WINDOW_END = "23:59"

# Screen filters — permissive placeholder bounds (real criteria in settings_local).
PRICE_MIN = 1
PRICE_MAX = 100_000
MIN_MARKET_CAP = 0
LOSER_MIN_VOLUME_RATIO = 1.0
LOSER_MIN_PCT_LOSS = 0.0
SCREEN_MIN_ABS_PCT_CHANGE = 0.0  # min |day % change| to list (0 = no filter)

# Overnight (swing) pre-screen: RSI-only oversold confluence across two
# timeframes, run off-hours over a curated watchlist. A name survives only
# if RSI is oversold on BOTH a slow and a fast timeframe. Generic defaults here;
# real values may override in settings_local. (No Bollinger on this path — the
# swing screen is RSI-only by design.)
PRESCREEN_SLOW_HOURS = 4  # slow timeframe (hours per bar)
PRESCREEN_SLOW_LOOKBACK_DAYS = 90  # ~3 months of slow bars
PRESCREEN_FAST_HOURS = 1  # fast timeframe (hours per bar)
PRESCREEN_FAST_LOOKBACK_DAYS = 30  # ~1 month of fast bars
PRESCREEN_RSI_THRESHOLD = RSI_OVERSOLD  # reuse the standard oversold line
PRESCREEN_WATCHLIST_PATH = "alertengine/data/watchlist.xls"  # git-ignored input
PRESCREEN_OUTPUT_PATH = "candidates.csv"  # scheduled survivors (git-ignored *.csv)
PRESCREEN_TIMEOUT_SECONDS = 300  # keep an Alpaca/API stall from wedging the bot
MANUAL_WATCHLIST_PATH = "alertengine/data/manual_watchlist.txt"

# De-dup / cooldown: min 2-min bars after a buy alert (and setup must clear)
# before a symbol can re-arm. Prevents re-firing on the same oversold episode.
COOLDOWN_BARS = 5

# Stage-2 confirmation (two-stage state machine):
#   layer-1 setup only *arms* a symbol; a BUY fires on CONFIRM_GREEN_BARS
#   consecutive green 2-min closes. If it doesn't confirm within
#   ARM_TIMEOUT_BARS 2-min bars (15 = 30 min), the symbol resets to scratch.
CONFIRM_GREEN_BARS = 2
# Exit mirror: an overbought setup arms the SELL side; a SELL fires on
# CONFIRM_RED_BARS consecutive red 2-min closes (same arm-timeout/cooldown).
CONFIRM_RED_BARS = 2
ARM_TIMEOUT_BARS = 15

# Local overrides (git-ignored): real confirmed params, applied last so they win.
try:
    from .settings_local import *  # noqa: F401,F403
except ImportError:
    pass
