import asyncio
import subprocess
from datetime import datetime
from typing import AsyncIterator

from alertengine import settings
from alertengine.engine import AlertEngine
from alertengine.gate import ApprovalGate
from alertengine.interfaces import AlertRule, DataFeed, Notifier
from alertengine.models import Alert, Bar
from alertengine.screeners.mock_screener import MockScreener
from alertengine.strategy import StrategyConfig
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


def _strategy_engine(feed):
    slow = StrategyConfig(name="slow", rule=_Rule(), bar_interval_minutes=2)
    fast = StrategyConfig(name="fast", rule=_Rule(), bar_interval_minutes=1)
    return AlertEngine(
        screener=MockScreener(),
        feed=feed,
        rule=slow.rule,
        notifier=_Notifier(),
        gate=ApprovalGate(),
        strategies={"slow": slow, "fast": fast},
        strategy_name="slow",
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


def test_controller_can_use_an_independent_watchlist_path(tmp_path):
    async def drive():
        path = tmp_path / "penny.txt"
        controller = WatchController(
            _engine(_Feed()),
            manual_watchlist_path=str(path),
            manual_s3_uri="",
            task_name="penny-market-watch",
        )
        await controller.watch_many("AMC CELZ")
        await controller.stop()
        assert path.read_text() == "AMC\nCELZ\n"

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


def test_replace_automatic_removes_stale_but_preserves_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(tmp_path / "watch.txt"))

    async def drive():
        feed = _Feed()
        controller = WatchController(_engine(feed))
        controller.load_automatic(["AAPL", "MSFT"])
        await controller.watch("AAPL")  # overlap: AAPL is also explicitly manual
        await asyncio.sleep(0)

        watchlist = await controller.replace_automatic(["NVDA"], start=True)
        await asyncio.sleep(0)

        assert watchlist == ["AAPL", "NVDA"]
        assert controller.automatic_symbols == ["NVDA"]
        assert controller.manual_symbols == ["AAPL"]
        assert feed.subscriptions[-1] == ["AAPL", "NVDA"]
        await controller.stop()

    asyncio.run(drive())


def test_manual_changes_upload_to_s3_when_configured(tmp_path, monkeypatch):
    path = tmp_path / "watch.txt"
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(path))
    monkeypatch.setenv(
        "MANUAL_WATCHLIST_S3_URI",
        "s3://private-bucket/private/runtime/manual_watchlist.txt",
    )
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs, path.read_text()))

    monkeypatch.setattr("alertengine.watch_controller.subprocess.run", fake_run)

    async def drive():
        controller = WatchController(_engine(_Feed()))
        await controller.watch_many("AAPL MSFT")
        await controller.stop()
        assert controller.persistence_status == {
            "s3_configured": True,
            "last_error": None,
        }

    asyncio.run(drive())

    assert calls[0][0] == [
        "aws",
        "s3",
        "cp",
        str(path),
        "s3://private-bucket/private/runtime/manual_watchlist.txt",
        "--region",
        "us-east-1",
    ]
    assert calls[0][1]["timeout"] == 20
    assert calls[0][2] == "AAPL\nMSFT\n"


def test_s3_failure_keeps_local_watchlist_and_surfaces_status(tmp_path, monkeypatch):
    path = tmp_path / "watch.txt"
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(path))
    monkeypatch.setenv("MANUAL_WATCHLIST_S3_URI", "s3://private-bucket/manual.txt")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="upload denied")

    monkeypatch.setattr("alertengine.watch_controller.subprocess.run", fail)

    async def drive():
        controller = WatchController(_engine(_Feed()))
        await controller.watch("AAPL")
        await controller.stop()
        assert controller.persistence_status["last_error"] == "upload denied"

    asyncio.run(drive())
    assert path.read_text() == "AAPL\n"


def test_batch_watch_and_unwatch_restart_once_and_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(tmp_path / "watch.txt"))

    async def drive():
        feed = _Feed()
        controller = WatchController(_engine(feed))

        added, invalid, watchlist = await controller.watch_many("aapl  msft AAPL")
        await asyncio.sleep(0)
        assert added == ["AAPL", "MSFT"]
        assert invalid == []
        assert watchlist == ["AAPL", "MSFT"]
        assert feed.subscriptions == [["AAPL", "MSFT"]]
        assert (tmp_path / "watch.txt").read_text() == "AAPL\nMSFT\n"

        removed, invalid, watchlist = await controller.unwatch_many("aapl msft")
        assert removed == ["AAPL", "MSFT"]
        assert invalid == []
        assert watchlist == []
        assert not controller.running
        assert (tmp_path / "watch.txt").read_text() == ""

    asyncio.run(drive())


def test_batch_skips_invalid_symbols_and_applies_valid_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(tmp_path / "watch.txt"))

    async def drive():
        feed = _Feed()
        controller = WatchController(_engine(feed))
        added, invalid, watchlist = await controller.watch_many(
            "AAPL not/a/ticker MSFT bad$ AAPL"
        )
        await asyncio.sleep(0)

        assert added == ["AAPL", "MSFT"]
        assert invalid == ["not/a/ticker", "bad$"]
        assert watchlist == ["AAPL", "MSFT"]
        assert feed.subscriptions == [["AAPL", "MSFT"]]
        assert (tmp_path / "watch.txt").read_text() == "AAPL\nMSFT\n"

        removed, invalid, watchlist = await controller.unwatch_many(
            "AAPL invalid/ticker MSFT"
        )
        assert removed == ["AAPL", "MSFT"]
        assert invalid == ["invalid/ticker"]
        assert watchlist == []
        assert not controller.running

    asyncio.run(drive())


def test_all_invalid_batch_makes_no_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MANUAL_WATCHLIST_PATH", str(tmp_path / "watch.txt"))

    async def drive():
        feed = _Feed()
        controller = WatchController(_engine(feed))
        added, invalid, watchlist = await controller.watch_many("bad$ also/bad")

        assert added == []
        assert invalid == ["bad$", "also/bad"]
        assert watchlist == []
        assert feed.subscriptions == []
        assert not controller.running
        assert not (tmp_path / "watch.txt").exists()

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


def test_strategy_switch_persists_and_restarts_active_subscription(tmp_path):
    async def drive():
        feed = _Feed()
        engine = _strategy_engine(feed)
        engine.gate.approve("AAPL")
        path = tmp_path / "strategy.txt"
        controller = WatchController(
            engine,
            strategy_path=str(path),
            strategy_s3_uri="",
        )
        await controller.start()
        await asyncio.sleep(0)

        active, changed = await controller.select_strategy("fast")
        await asyncio.sleep(0)

        assert (active, changed) == ("fast", True)
        assert path.read_text() == "fast\n"
        assert feed.subscriptions == [["AAPL"], ["AAPL"]]
        await controller.stop()

    asyncio.run(drive())


def test_persisted_strategy_is_loaded_on_startup(tmp_path):
    path = tmp_path / "strategy.txt"
    path.write_text("fast\n")
    controller = WatchController(
        _strategy_engine(_Feed()),
        strategy_path=str(path),
        strategy_s3_uri="",
    )

    assert controller.load_strategy() == "fast"
    assert controller.engine.bar_interval_minutes == 1


def test_invalid_persisted_strategy_keeps_configured_default(tmp_path, caplog):
    path = tmp_path / "strategy.txt"
    path.write_text("missing\n")
    controller = WatchController(
        _strategy_engine(_Feed()),
        strategy_path=str(path),
        strategy_s3_uri="",
    )

    assert controller.load_strategy() == "slow"
    assert "ignoring invalid persisted strategy" in caplog.text


def test_strategy_switch_uploads_selection_when_configured(tmp_path, monkeypatch):
    path = tmp_path / "strategy.txt"
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs, path.read_text()))

    monkeypatch.setattr("alertengine.watch_controller.subprocess.run", fake_run)

    async def drive():
        controller = WatchController(
            _strategy_engine(_Feed()),
            strategy_path=str(path),
            strategy_s3_uri="s3://private-bucket/private/runtime/active_strategy.txt",
        )
        await controller.select_strategy("fast")
        assert controller.strategy_persistence_status == {
            "s3_configured": True,
            "last_error": None,
        }

    asyncio.run(drive())

    assert calls[0][0] == [
        "aws",
        "s3",
        "cp",
        str(path),
        "s3://private-bucket/private/runtime/active_strategy.txt",
    ]
    assert calls[0][1]["timeout"] == 20
    assert calls[0][2] == "fast\n"
