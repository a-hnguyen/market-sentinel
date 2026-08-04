"""Run the post-close pre-screen once and write next session's candidates.

    python -m alertengine.prescreen

Reads ALPACA_API_KEY / ALPACA_SECRET_KEY from .env, loads the curated watchlist,
runs the RSI 4h/1h confluence over Alpaca historical bars, and writes survivors
to a CSV for the next session. Runs any time from historical data; production
invokes it after the regular session closes.
In AWS this becomes the EventBridge-scheduled Lambda edge; the pipeline
(run_prescreen) is identical, only the sink changes.

Because a weekday schedule still fires on market holidays, this skips on non-trading
days (writing nothing, so a stale-dated CSV isn't produced). Pass --force to run
anyway, e.g. for an explicit manual rerun.
"""

import logging
import sys

from dotenv import load_dotenv

from .. import settings
from ..logging_config import configure_logging
from .calendar import is_trading_day, today_et
from .reporting import send_discord_summary, summary_messages
from .runner import run_prescreen_report


def main() -> int:
    configure_logging("prescreen")
    log = logging.getLogger("alertengine.prescreen")
    load_dotenv()
    log.info(
        "event=prescreen_start force=%s notify=%s",
        "--force" in sys.argv[1:],
        "--notify" in sys.argv[1:],
    )

    if "--force" not in sys.argv[1:] and not is_trading_day():
        log.info("event=prescreen_skip reason=non_trading_day date=%s", today_et())
        print(f"{today_et()} is not a trading day; skipping (use --force to run).")
        return 0

    try:
        report = run_prescreen_report()
    except FileNotFoundError:
        log.exception("event=prescreen_failed reason=watchlist_missing")
        print(
            f"watchlist not found at {settings.PRESCREEN_WATCHLIST_PATH!r} — "
            "put the curated .xls/.csv there (git-ignored)."
        )
        return 1
    except RuntimeError as e:  # missing Alpaca credentials
        log.exception("event=prescreen_failed reason=runtime_error")
        print(f"error: {e}")
        return 1

    slow_label = f"rsi_{settings.PRESCREEN_SLOW_HOURS}h"
    fast_label = f"rsi_{settings.PRESCREEN_FAST_HOURS}h"
    print(
        f"{len(report.results)} overbought/oversold candidate(s) -> "
        f"{settings.PRESCREEN_OUTPUT_PATH!r}"
    )
    for r in report.results:
        print(
            f"  {r.symbol:6} {r.signal:10} {slow_label}={r.rsi_slow:5.1f}  "
            f"{fast_label}={r.rsi_fast:5.1f}  {r.category}"
        )
    for message in summary_messages(report):
        print(message.replace("**", ""))
    log.info(
        "event=prescreen_complete candidates=%s count=%d",
        ",".join(result.symbol for result in report.results),
        len(report.results),
    )
    if "--notify" in sys.argv[1:]:
        try:
            send_discord_summary(report)
        except Exception as exc:
            log.exception("event=prescreen_notification_failed")
            print(f"Discord pre-screen summary failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
