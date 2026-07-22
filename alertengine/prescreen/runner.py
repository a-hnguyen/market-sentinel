"""One-call post-close pipeline: watchlist -> RSI confluence -> candidates CSV.

Shared by all three entry points so the logic lives in exactly one place:
  * `python -m alertengine.prescreen`      (standalone / scheduled)
  * `python -m alertengine --prescreen`    (refresh at engine startup)
  * the REPL/Discord `prescreen` command   (refresh on demand)

Raises FileNotFoundError if the watchlist is missing and RuntimeError if Alpaca
credentials aren't set (from AlpacaFeed) — callers decide how to report those.
"""

from .. import settings
from .screener import PreScreener, PreScreenReport, ScreenResult
from .reporting import save_report
from .sinks import CsvSink, load_candidates
from .watchlist import read_watchlist


def run_prescreen_report(feed=None) -> PreScreenReport:
    """Run the scan once, write survivors to the candidates CSV, return them.

    `feed` is injectable for tests; in production it defaults to a real
    AlpacaFeed (constructed lazily so importing this module needs no creds).
    """
    watchlist = read_watchlist(settings.PRESCREEN_WATCHLIST_PATH)
    if feed is None:
        from ..feeds.alpaca_feed import AlpacaFeed

        feed = AlpacaFeed()
    try:
        previous = set(load_candidates(settings.PRESCREEN_OUTPUT_PATH))
    except FileNotFoundError:
        previous = set()
    report = PreScreener(feed).run_report(watchlist)
    current = {result.symbol for result in report.results}
    report.added = sorted(current - previous)
    report.removed = sorted(previous - current)
    CsvSink(
        settings.PRESCREEN_OUTPUT_PATH,
        slow_label=f"rsi_{settings.PRESCREEN_SLOW_HOURS}h",
        fast_label=f"rsi_{settings.PRESCREEN_FAST_HOURS}h",
    ).write(report.results)
    save_report(report, settings.PRESCREEN_REPORT_PATH)
    return report


def run_prescreen(feed=None) -> list[ScreenResult]:
    """Run the scan and return its final intersection (legacy public API)."""
    return run_prescreen_report(feed).results
