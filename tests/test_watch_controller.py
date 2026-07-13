import asyncio
from datetime import datetime
from typing import AsyncIterator

from alertengine import settings
from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import AlertRule, DataFeed, Notifier
from alertengine.models import Alert, Bar
from alertengine.screeners.mock_screener import MockScreener
from alertengine.watch_controller import WatchController


class _Feed(DataFeed):
    def __init__(self):
        self.subscriptions = []

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        self.subscriptions.append(list(symbols))
        await asyncio.Event().wait()
        yield Bar("X", datetime.now(), 1, 1, 1, 1, 1)  # pragma: no cover


class _Rule(AlertRule):
    def evaluate(self, symbol, bars):
        return None


class _Notifier(Notifier):
    async def send(self, alert: Alert) -> None:
        pass


def _engine(feed):
    return AlertEngine(
        screener=MockScreener(),
        feed=feed,
        rule=_Rule(),
        notifier=_Notifier(),
        gate=ApprovalGate(),
    )


def test_watch_restarts_subscription_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(tmp_path / "watch.txt"))

    async def drive():
        feed = _Feed()
        controller = WatchController(_engine(feed), retry_seconds=0)
        await controller.watch("aapl")
        await asyncio.sleep(0)
        await controller.watch("nvda")
        await asyncio.sleep(0)
        await controller.watch("NVDA")  # idempotent: no needless reconnect
        await asyncio.sleep(0)
        assert feed.subscriptions == [["AAPL"], ["AAPL", "NVDA"]]
        assert controller.active_symbols == ["AAPL", "NVDA"]
        await controller.replace_from_gate(start=True)
        await asyncio.sleep(0)
        assert feed.subscriptions == [["AAPL"], ["AAPL", "NVDA"]]
        assert (tmp_path / "watch.txt").read_text() == "AAPL\nNVDA\n"
        assert not (tmp_path / "watch.txt.tmp").exists()
        await controller.stop()

    asyncio.run(drive())


def test_unwatch_restarts_or_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(tmp_path / "watch.txt"))

    async def drive():
        feed = _Feed()
        controller = WatchController(_engine(feed))
        await controller.watch("AAPL")
        await asyncio.sleep(0)
        await controller.watch("NVDA")
        await asyncio.sleep(0)
        await controller.unwatch("AAPL")
        await asyncio.sleep(0)
        assert feed.subscriptions[-1] == ["NVDA"]
        subscriptions = len(feed.subscriptions)
        await controller.unwatch("AAPL")  # idempotent: no needless reconnect
        await asyncio.sleep(0)
        assert len(feed.subscriptions) == subscriptions
        await controller.unwatch("NVDA")
        assert not controller.running
        assert controller.active_symbols == []

    asyncio.run(drive())


def test_invalid_symbol_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(tmp_path / "watch.txt"))

    async def drive():
        controller = WatchController(_engine(_Feed()))
        try:
            await controller.watch("AAPL; shutdown")
        except ValueError as exc:
            assert "invalid stock symbol" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("invalid symbol accepted")

    asyncio.run(drive())
