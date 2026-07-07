"""Unit tests for NtfyNotifier and MultiNotifier (no real network)."""

import asyncio
from datetime import datetime, timezone

import pytest

from alertengine.interfaces import Notifier
from alertengine.models import Alert
from alertengine.notifiers.multi_notifier import MultiNotifier
from alertengine.notifiers.ntfy_notifier import NtfyNotifier


def _alert() -> Alert:
    return Alert(
        symbol="OUST",
        timestamp=datetime(2026, 7, 2, 14, 30, tzinfo=timezone.utc),
        rule="bb_rsi_layer1",
        message="close 41.15 < lower BB 41.26 and RSI 11.1 < 30",
        context={
            "close": 41.15,
            "bb_lower": 41.26,
            "rsi": 11.1,
            "pct_change": -17.0,
            "volume_ratio": 2.0,
        },
    )


def test_missing_topic_raises(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    with pytest.raises(RuntimeError, match="ntfy topic"):
        NtfyNotifier()


def test_body_includes_change_and_volume():
    n = NtfyNotifier(topic="t")
    body = n._body(_alert())
    # One field per line, colon-delimited.
    assert "close:   41.15" in body
    assert "RSI:     11.1" in body
    assert "day:     -17.0%" in body
    assert "rel vol: x2.0" in body
    assert "\n" in body


def test_send_posts_to_topic_url_with_headers(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["headers"] = req.headers

        class _Resp:  # context-manager-ish stand-in; not used
            pass

        return _Resp()

    monkeypatch.setattr(
        "alertengine.notifiers.ntfy_notifier.urllib.request.urlopen", fake_urlopen
    )
    n = NtfyNotifier(topic="my-topic", server="https://ntfy.sh", token="secret")
    asyncio.run(n.send(_alert()))

    assert captured["url"] == "https://ntfy.sh/my-topic"
    assert b"close:   41.15" in captured["data"]
    # urllib capitalizes header keys.
    assert captured["headers"]["Title"] == "OUST - layer-1 alert"
    # Title must be latin-1 encodable (HTTP header constraint).
    captured["headers"]["Title"].encode("latin-1")
    assert captured["headers"]["Priority"] == "high"
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_send_swallows_network_errors(monkeypatch, capsys):
    def boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(
        "alertengine.notifiers.ntfy_notifier.urllib.request.urlopen", boom
    )
    n = NtfyNotifier(topic="t")
    # Must not raise — a push failure can't be allowed to kill the watch loop.
    asyncio.run(n.send(_alert()))
    assert "push failed" in capsys.readouterr().out


def test_multi_notifier_fans_out_and_isolates_failures():
    class Recorder(Notifier):
        def __init__(self):
            self.got = []

        async def send(self, alert):
            self.got.append(alert)

    class Broken(Notifier):
        async def send(self, alert):
            raise RuntimeError("boom")

    a, b = Recorder(), Recorder()
    multi = MultiNotifier([a, Broken(), b])
    asyncio.run(multi.send(_alert()))
    # Both working notifiers still received it despite the broken one.
    assert len(a.got) == 1 and len(b.got) == 1
