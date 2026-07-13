"""Run the overnight pre-screen once and write tonight's candidates.

    python -m alertengine.prescreen

Reads ALPACA_API_KEY / ALPACA_SECRET_KEY from .env, loads the curated watchlist,
runs the RSI 4h/1h confluence over Alpaca historical bars, and writes survivors
to a CSV for the morning. Runs any time (historical data), so it can be invoked
the night before or by the deployed pre-market schedule.
In AWS this becomes the EventBridge-scheduled Lambda edge; the pipeline
(run_prescreen) is identical, only the sink changes.

Because a weekday schedule still fires on market holidays, this skips on non-trading
days (writing nothing, so a stale-dated CSV isn't produced). Pass --force to run
anyway, e.g. to pre-build the night before from the prior session's bars.
"""

import sys

from dotenv import load_dotenv

from .. import settings
from .calendar import is_trading_day, today_et
from .runner import run_prescreen


def main() -> int:
    load_dotenv()

    if "--force" not in sys.argv[1:] and not is_trading_day():
        print(f"{today_et()} is not a trading day; skipping (use --force to run).")
        return 0

    try:
        results = run_prescreen()
    except FileNotFoundError:
        print(
            f"watchlist not found at {settings.PRESCREEN_WATCHLIST_PATH!r} — "
            "put the curated .xls/.csv there (git-ignored)."
        )
        return 1
    except RuntimeError as e:  # missing Alpaca credentials
        print(f"error: {e}")
        return 1

    slow_label = f"rsi_{settings.PRESCREEN_SLOW_HOURS}h"
    fast_label = f"rsi_{settings.PRESCREEN_FAST_HOURS}h"
    print(f"{len(results)} oversold survivor(s) -> {settings.PRESCREEN_OUTPUT_PATH!r}")
    for r in results:
        print(
            f"  {r.symbol:6} {slow_label}={r.rsi_slow:5.1f}  "
            f"{fast_label}={r.rsi_fast:5.1f}  {r.category}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
