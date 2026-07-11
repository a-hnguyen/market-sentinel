"""ConsoleNotifier: stage labelling, Pacific-time display, and column layout.

Each test points the notifier at a temp logfile so the repo's alerts.log is
never touched, and captures stdout via capsys.

asyncio.run rather than a pytest-asyncio marker, to avoid the plugin.
"""

import asyncio
from datetime import datetime, timezone

from alertengine.models import Alert
from alertengine.notifiers.console_notifier import ConsoleNotifier


def _send(notifier, alert):
    asyncio.run(notifier.send(alert))


def _watch_alert(**ctx):
    base = {"close": 41.15, "bb_lower": 41.26, "rsi": 11.1}
    base.update(ctx)
    return Alert(
        symbol="OUST",
        # 14:30 UTC in July -> 07:30 Pacific Daylight (UTC-7).
        timestamp=datetime(2026, 7, 2, 14, 30, tzinfo=timezone.utc),
        rule="bb_rsi_layer1",
        message="close 41.15 < lower BB 41.26 and RSI 11.1 < 30",
        context=base,
        kind="watch",
    )


def test_watch_is_labelled_and_shown_in_pacific(tmp_path, capsys):
    _send(ConsoleNotifier(logfile=str(tmp_path / "a.log")), _watch_alert())
    out = capsys.readouterr().out
    assert "[WATCH" in out
    assert "OUST" in out
    # 14:30 UTC -> 07:30 PDT, and the zone is shown.
    assert "2026-07-02 07:30 PDT" in out


def test_buy_is_labelled_buy(tmp_path, capsys):
    buy = Alert(
        symbol="OUST",
        timestamp=datetime(2026, 7, 2, 14, 40, tzinfo=timezone.utc),
        rule="bb_rsi_buy",
        message="BUY OUST: 2 green 2-min closes confirmed",
        context={"close": 42.00},  # no bb_lower/rsi -> falls back to message
        kind="buy",
    )
    _send(ConsoleNotifier(logfile=str(tmp_path / "a.log")), buy)
    out = capsys.readouterr().out
    assert "[BUY" in out
    assert "2 green 2-min closes confirmed" in out  # fallback message shown


def test_unknown_kind_falls_back_to_alert_label(tmp_path, capsys):
    a = _watch_alert()
    a.kind = "something-else"
    _send(ConsoleNotifier(logfile=str(tmp_path / "a.log")), a)
    assert "[ALERT" in capsys.readouterr().out


def test_numeric_context_builds_columns(tmp_path, capsys):
    _send(ConsoleNotifier(logfile=str(tmp_path / "a.log")), _watch_alert())
    out = capsys.readouterr().out
    assert "close" in out and "lower BB" in out and "RSI" in out


def test_screening_fields_shown_when_present(tmp_path, capsys):
    _send(
        ConsoleNotifier(logfile=str(tmp_path / "a.log")),
        _watch_alert(pct_change=-17.0, volume_ratio=2.0),
    )
    out = capsys.readouterr().out
    assert "chg" in out and "-17.0%" in out
    assert "volx" in out and "2.0" in out


def test_naive_timestamp_treated_as_utc(tmp_path, capsys):
    # A naive (mock) timestamp must be treated as UTC, not local, before the
    # Pacific conversion — otherwise mock alerts show the wrong time.
    a = _watch_alert()
    a.timestamp = datetime(2026, 7, 2, 14, 30)  # naive
    _send(ConsoleNotifier(logfile=str(tmp_path / "a.log")), a)
    assert "07:30 PDT" in capsys.readouterr().out


def test_logs_to_file(tmp_path):
    logfile = tmp_path / "alerts.log"
    _send(ConsoleNotifier(logfile=str(logfile)), _watch_alert())
    contents = logfile.read_text()
    assert "OUST" in contents and "bb_rsi_layer1" in contents
