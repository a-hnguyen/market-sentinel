"""Overnight pre-screen: watchlist parsing, the RSI confluence, orchestration,
and the CSV sink. All pure/offline — a fake feed stands in for Alpaca so no
network or keys are needed (mirrors the fetch/filter split used elsewhere).
"""

import csv
import os

import pandas as pd
import pytest

from alertengine import settings
from alertengine.prescreen.screener import (
    PreScreener,
    ScreenResult,
    evaluate_confluence,
)
from alertengine.prescreen.sinks import CsvSink, load_candidates
from alertengine.prescreen.watchlist import read_watchlist

# --- helpers ---------------------------------------------------------------

# A monotonically falling series drives RSI toward 0 (all losses, no gains);
# a rising series drives it toward 100. Long enough for RSI(14).
FALLING = [100.0 - i for i in range(40)]
RISING = [10.0 + i for i in range(40)]


class _FakeFeed:
    """Returns canned closes per (symbol) regardless of timeframe, but records
    the timeframes it was asked for so we can assert both were fetched."""

    def __init__(self, closes_by_symbol, per_tf=None):
        self._closes = closes_by_symbol
        self._per_tf = per_tf or {}  # {(sym, hours): [closes]} overrides
        self.calls = []

    def fetch_closes(self, symbols, hours, lookback_days):
        self.calls.append(hours)
        out = {}
        for s in symbols:
            key = (s.upper(), hours)
            if key in self._per_tf:
                out[s.upper()] = self._per_tf[key]
            else:
                out[s.upper()] = self._closes.get(s.upper(), [])
        return out


# --- evaluate_confluence (pure) -------------------------------------------


def test_confluence_true_when_both_timeframes_oversold():
    verdict = evaluate_confluence(FALLING, FALLING, rsi_period=14, threshold=30)
    assert verdict is not None
    oversold, r_slow, r_fast = verdict
    assert oversold is True
    assert r_slow < 30 and r_fast < 30


def test_confluence_false_when_only_one_side_oversold():
    # Slow oversold (falling), fast not (rising) -> confluence fails.
    verdict = evaluate_confluence(FALLING, RISING, rsi_period=14, threshold=30)
    oversold, r_slow, r_fast = verdict
    assert r_slow < 30 and r_fast > 30
    assert oversold is False


def test_confluence_none_when_insufficient_history():
    # 10 closes < rsi_period+1 -> cannot compute; skip signal.
    assert evaluate_confluence(FALLING[:10], FALLING, 14, 30) is None
    assert evaluate_confluence(FALLING, FALLING[:10], 14, 30) is None


# --- PreScreener.run -------------------------------------------------------


def test_run_keeps_only_dual_oversold_and_fetches_both_timeframes():
    feed = _FakeFeed(
        {},
        per_tf={
            # HOT is oversold on both 4h and 1h; WARM only on the slow side.
            ("HOT", 4): FALLING,
            ("HOT", 1): FALLING,
            ("WARM", 4): FALLING,
            ("WARM", 1): RISING,
        },
    )
    ps = PreScreener(feed, slow_hours=4, fast_hours=1, rsi_period=14, rsi_threshold=30)
    results = ps.run([("HOT", "cat-a"), ("WARM", "cat-b")])

    assert [r.symbol for r in results] == ["HOT"]
    assert results[0].category == "cat-a"
    # Both timeframes were queried (4h and 1h), one batched call each.
    assert sorted(feed.calls) == [1, 4]


def test_run_sorts_most_oversold_first():
    # DEEP is a pure monotonic fall (all losses -> RSI ~0). SHALLOW mixes small
    # up-ticks in (some gains -> RSI > 0 but still < 30). So DEEP is the more
    # oversold and must rank first, regardless of input order.
    deep = [100.0 - 3 * i for i in range(40)]
    shallow = [100.0]
    for i in range(1, 40):
        shallow.append(shallow[-1] + (0.5 if i % 2 else -2.0))  # sawtooth, net down
    feed = _FakeFeed({"DEEP": deep, "SHALLOW": shallow})
    ps = PreScreener(feed, rsi_period=14, rsi_threshold=30)
    results = ps.run([("SHALLOW", ""), ("DEEP", "")])
    # Both survive (both oversold), sorted by combined RSI ascending, DEEP first.
    assert [r.symbol for r in results] == ["DEEP", "SHALLOW"]
    combined = [r.rsi_slow + r.rsi_fast for r in results]
    assert combined == sorted(combined)


def test_run_skips_symbols_with_thin_history():
    feed = _FakeFeed({"OK": FALLING, "THIN": FALLING[:5]})
    ps = PreScreener(feed, rsi_period=14, rsi_threshold=30)
    results = ps.run([("OK", ""), ("THIN", "")])
    assert [r.symbol for r in results] == ["OK"]  # THIN skipped, no crash


def test_run_empty_watchlist_makes_no_calls():
    feed = _FakeFeed({})
    ps = PreScreener(feed)
    assert ps.run([]) == []
    assert feed.calls == []  # short-circuits before touching the feed


# --- watchlist reader ------------------------------------------------------


# The dedup/uppercase/blank/category logic is shared across .csv and .xls (it
# runs after pandas reads either), so it's exercised via CSV — no Excel-writer
# dependency needed. A separate smoke test covers the real .xls read path.


def _write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def test_read_watchlist_dedups_and_uppercases(tmp_path):
    p = tmp_path / "wl.csv"
    _write_csv(
        p,
        [
            {"Ticker": "aapl", "List": "favs"},
            {"Ticker": "AAPL", "List": "dup"},  # duplicate, first wins
            {"Ticker": "tsla", "List": "BIG LOSERS"},
        ],
    )
    wl = read_watchlist(str(p))
    assert wl == [("AAPL", "favs"), ("TSLA", "BIG LOSERS")]


def test_read_watchlist_skips_blank_tickers(tmp_path):
    p = tmp_path / "wl.csv"
    _write_csv(p, [{"Ticker": "NVDA", "List": "x"}, {"Ticker": None, "List": "y"}])
    assert read_watchlist(str(p)) == [("NVDA", "x")]


def test_read_watchlist_category_optional(tmp_path):
    p = tmp_path / "wl.csv"
    _write_csv(p, [{"Ticker": "MSFT"}])
    assert read_watchlist(str(p)) == [("MSFT", "")]


@pytest.mark.skipif(
    not os.path.exists(settings.PRESCREEN_WATCHLIST_PATH),
    reason="real .xls watchlist not present (git-ignored input)",
)
def test_read_real_xls_watchlist():
    # Proves the .xls read path (xlrd) works against the real watchlist file.
    wl = read_watchlist(settings.PRESCREEN_WATCHLIST_PATH)
    assert len(wl) > 0
    assert all(sym.isupper() and sym for sym, _ in wl)
    assert len({sym for sym, _ in wl}) == len(wl)  # de-duplicated


# --- CsvSink ---------------------------------------------------------------


def test_csv_sink_writes_header_and_rows(tmp_path):
    from datetime import datetime, timezone

    out = tmp_path / "candidates.csv"
    ts = datetime(2026, 7, 9, 23, 0, tzinfo=timezone.utc)
    CsvSink(str(out), slow_label="rsi_4h", fast_label="rsi_1h").write(
        [ScreenResult("OUST", 12.3, 8.9, "favs", ts)]
    )
    rows = list(csv.reader(out.open()))
    assert rows[0] == ["Ticker", "rsi_4h", "rsi_1h", "category", "scanned_at"]
    assert rows[1][:4] == ["OUST", "12.3", "8.9", "favs"]
    assert rows[1][4].startswith("2026-07-09T23:00")


def test_csv_sink_overwrites_previous_run(tmp_path):
    out = tmp_path / "candidates.csv"
    from datetime import datetime, timezone

    ts = datetime(2026, 7, 9, tzinfo=timezone.utc)
    sink = CsvSink(str(out))
    sink.write([ScreenResult("AAA", 5.0, 5.0, "", ts)])
    sink.write([ScreenResult("BBB", 6.0, 6.0, "", ts)])  # replaces, not appends
    rows = list(csv.reader(out.open()))
    assert len(rows) == 2  # header + one row
    assert rows[1][0] == "BBB"


# --- load_candidates (read-back for auto-approve) --------------------------


def test_load_candidates_round_trips_the_sink(tmp_path):
    from datetime import datetime, timezone

    out = tmp_path / "candidates.csv"
    ts = datetime(2026, 7, 9, tzinfo=timezone.utc)
    CsvSink(str(out)).write(
        [
            ScreenResult("QFIN", 29.6, 21.2, "New 52 lows", ts),
            ScreenResult("LAC", 25.0, 27.9, "Materials", ts),
        ]
    )
    assert load_candidates(str(out)) == ["QFIN", "LAC"]  # tickers only, in order


def test_load_candidates_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_candidates(str(tmp_path / "nope.csv"))


def test_load_candidates_empty_survivor_list(tmp_path):
    out = tmp_path / "candidates.csv"
    CsvSink(str(out)).write([])  # header only, no survivors tonight
    assert load_candidates(str(out)) == []


# --- run_prescreen (shared pipeline) ---------------------------------------


# --- trading-day guard -----------------------------------------------------


class _FakeCalClient:
    """Stands in for Alpaca's TradingClient: returns a calendar entry only for
    the days listed as open, mirroring the real API (holidays are absent)."""

    def __init__(self, open_days):
        self._open = set(open_days)

    def get_calendar(self, req):
        from types import SimpleNamespace

        # Real client returns entries only for trading days in [start, end].
        return [SimpleNamespace(date=req.start)] if req.start in self._open else []


def test_is_trading_day_true_for_session_day():
    from datetime import date

    from alertengine.prescreen.calendar import is_trading_day

    day = date(2026, 7, 6)  # Monday, regular session
    assert is_trading_day(day, client=_FakeCalClient([day])) is True


def test_is_trading_day_false_for_holiday():
    from datetime import date

    from alertengine.prescreen.calendar import is_trading_day

    holiday = date(2026, 7, 3)  # Independence Day (observed) — market closed
    assert is_trading_day(holiday, client=_FakeCalClient([])) is False


def test_is_trading_day_fails_open_on_error():
    from datetime import date

    from alertengine.prescreen.calendar import is_trading_day

    class _Boom:
        def get_calendar(self, req):
            raise RuntimeError("calendar API down")

    # An API error must not silently skip a real trading day: default to True.
    assert is_trading_day(date(2026, 7, 6), client=_Boom()) is True


def test_run_prescreen_writes_csv_and_returns_survivors(tmp_path, monkeypatch):
    from alertengine.prescreen import runner

    # Redirect the watchlist + output to temp files, inject a fake feed.
    wl = tmp_path / "wl.csv"
    _write_csv(wl, [{"Ticker": "HOT"}, {"Ticker": "COLD"}])
    out = tmp_path / "candidates.csv"
    monkeypatch.setattr(runner.settings, "PRESCREEN_WATCHLIST_PATH", str(wl))
    monkeypatch.setattr(runner.settings, "PRESCREEN_OUTPUT_PATH", str(out))

    feed = _FakeFeed({"HOT": FALLING, "COLD": RISING})  # only HOT is oversold
    results = runner.run_prescreen(feed=feed)

    assert [r.symbol for r in results] == ["HOT"]
    # The CSV was written and round-trips back to the same survivor.
    assert load_candidates(str(out)) == ["HOT"]
