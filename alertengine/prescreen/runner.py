"""One-call pre-screen pipeline: watchlist -> RSI confluence -> candidates CSV.

Shared by all three entry points so the logic lives in exactly one place:
  * `python -m alertengine.prescreen`      (standalone / scheduled)
  * `python -m alertengine --prescreen`    (refresh at engine startup)
  * the REPL/Discord `prescreen` command   (refresh on demand)

Raises FileNotFoundError if the watchlist is missing and RuntimeError if Alpaca
credentials aren't set (from AlpacaFeed) — callers decide how to report those.
"""

from .. import settings
from .screener import PreScreener, ScreenResult
from .sinks import CsvSink
from .watchlist import read_watchlist


def run_prescreen(feed=None) -> list[ScreenResult]:
    """Run the scan once, write survivors to the candidates CSV, return them.

    `feed` is injectable for tests; in production it defaults to a real
    AlpacaFeed (constructed lazily so importing this module needs no creds).
    """
    watchlist = read_watchlist(settings.PRESCREEN_WATCHLIST_PATH)
    if feed is None:
        from ..feeds.alpaca_feed import AlpacaFeed

        feed = AlpacaFeed()
    results = PreScreener(feed).run(watchlist)
    CsvSink(
        settings.PRESCREEN_OUTPUT_PATH,
        slow_label=f"rsi_{settings.PRESCREEN_SLOW_HOURS}h",
        fast_label=f"rsi_{settings.PRESCREEN_FAST_HOURS}h",
    ).write(results)
    return results
