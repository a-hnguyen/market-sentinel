"""Lifecycle control for a remotely managed watch subscription.

The engine evaluates bars; this controller owns the long-running watch task and
is the only place allowed to restart it when the approved symbol set changes.
That makes local and chat controls deterministic instead of merely mutating an
in-memory set that the already-open Alpaca websocket never sees.
"""

import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path

from . import settings
from .engine import AlertEngine

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class WatchController:
    def __init__(
        self,
        engine: AlertEngine,
        retry_seconds: float = 10.0,
        *,
        manual_watchlist_path: str | None = None,
        manual_s3_uri: str | None = None,
        task_name: str = "market-watch",
        log_name: str = "alertengine.watch",
    ) -> None:
        self.engine = engine
        self.retry_seconds = retry_seconds
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._enabled = False
        self._manual: set[str] = set()
        self._automatic: set[str] = set()
        self._manual_path = Path(
            manual_watchlist_path or settings.MANUAL_WATCHLIST_PATH
        )
        self._manual_s3_uri = (
            os.environ.get("MANUAL_WATCHLIST_S3_URI", "")
            if manual_s3_uri is None
            else manual_s3_uri
        ).strip()
        self._task_name = task_name
        self._aws_region = os.environ.get("AWS_REGION", "").strip()
        self._persistence_error: str | None = None
        self._active_symbols: tuple[str, ...] = ()
        self._log = logging.getLogger(log_name)

    @property
    def running(self) -> bool:
        return self._enabled and self._task is not None and not self._task.done()

    @property
    def active_symbols(self) -> list[str]:
        return list(self._active_symbols)

    @property
    def manual_symbols(self) -> list[str]:
        return sorted(self._manual)

    @property
    def automatic_symbols(self) -> list[str]:
        return sorted(self._automatic)

    @property
    def persistence_status(self) -> dict[str, str | bool | None]:
        return {
            "s3_configured": bool(self._manual_s3_uri),
            "last_error": self._persistence_error,
        }

    @staticmethod
    def normalize(symbol: str) -> str:
        value = symbol.strip().upper()
        if not _SYMBOL.fullmatch(value):
            raise ValueError(f"invalid stock symbol: {symbol!r}")
        return value

    @classmethod
    def partition_symbols(cls, symbols: str) -> tuple[list[str], list[str]]:
        """Partition whitespace-separated tickers into valid and invalid lists."""
        tokens = symbols.split()
        if not tokens:
            raise ValueError("enter at least one stock symbol")
        valid: list[str] = []
        invalid: list[str] = []
        for token in tokens:
            try:
                valid.append(cls.normalize(token))
            except ValueError:
                invalid.append(token)
        return list(dict.fromkeys(valid)), list(dict.fromkeys(invalid))

    def load_manual(self) -> list[str]:
        path = self._manual_path
        if not path.exists():
            return []
        symbols = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                symbols.append(self.normalize(line))
            except ValueError:
                self._log.warning("ignoring invalid persisted symbol %r", line)
        if symbols:
            self._manual.update(symbols)
            self.engine.gate.approve(*symbols)
        self._log.info(
            "event=manual_watchlist_loaded symbols=%s", ",".join(sorted(set(symbols)))
        )
        return sorted(set(symbols))

    def load_automatic(self, symbols: list[str]) -> list[str]:
        self._automatic = {self.normalize(symbol) for symbol in symbols}
        if self._automatic:
            self.engine.gate.approve(*self._automatic)
        self._log.info(
            "event=automatic_watchlist_loaded symbols=%s",
            ",".join(self.automatic_symbols),
        )
        return self.automatic_symbols

    async def _save_manual(self) -> None:
        path = self._manual_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            "".join(f"{s}\n" for s in sorted(self._manual)), encoding="utf-8"
        )
        temporary.replace(path)
        if not self._manual_s3_uri:
            self._persistence_error = None
            self._log.info(
                "event=manual_watchlist_persisted symbols=%s destination=local",
                ",".join(sorted(self._manual)),
            )
            return
        command = ["aws", "s3", "cp", str(path), self._manual_s3_uri]
        if self._aws_region:
            command.extend(["--region", self._aws_region])
        try:
            await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self._persistence_error = None
            self._log.info(
                "event=manual_watchlist_persisted symbols=%s destination=s3",
                ",".join(sorted(self._manual)),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            self._persistence_error = detail.strip()
            self._log.error(
                "manual watchlist saved locally but S3 upload failed: %s",
                self._persistence_error,
            )

    async def start(self) -> list[str]:
        async with self._lock:
            symbols = self.engine.gate.watchlist()
            if not symbols:
                raise ValueError("watchlist is empty")
            if self.running:
                return symbols
            self._enabled = True
            self._task = asyncio.create_task(self._supervise(), name=self._task_name)
            self._log.info("event=watch_enabled symbols=%s", ",".join(symbols))
            return symbols

    async def stop(self) -> None:
        async with self._lock:
            self._enabled = False
            await self._cancel_task()
            self._log.info("event=watch_disabled")

    async def watch(self, symbol: str) -> tuple[str, list[str]]:
        value = self.normalize(symbol)
        values, _invalid, watchlist = await self.watch_many(value)
        return values[0], watchlist

    async def watch_many(self, symbols: str) -> tuple[list[str], list[str], list[str]]:
        values, invalid = self.partition_symbols(symbols)
        if not values:
            return values, invalid, self.engine.gate.watchlist()
        async with self._lock:
            already_active = all(value in self._active_symbols for value in values)
            self.engine.gate.approve(*values)
            self._manual.update(values)
            self._log.info("event=manual_watchlist_add symbols=%s", ",".join(values))
            await self._save_manual()
            self._enabled = True
            if not (already_active and self.running):
                await self._restart_task()
            return values, invalid, self.engine.gate.watchlist()

    async def unwatch(self, symbol: str) -> tuple[str, list[str]]:
        value = self.normalize(symbol)
        values, _invalid, watchlist = await self.unwatch_many(value)
        return values[0], watchlist

    async def unwatch_many(
        self, symbols: str
    ) -> tuple[list[str], list[str], list[str]]:
        values, invalid = self.partition_symbols(symbols)
        if not values:
            return values, invalid, self.engine.gate.watchlist()
        async with self._lock:
            was_active = any(value in self._active_symbols for value in values)
            self.engine.gate.remove(*values)
            self._manual.difference_update(values)
            self._log.info("event=manual_watchlist_remove symbols=%s", ",".join(values))
            await self._save_manual()
            remaining = self.engine.gate.watchlist()
            if was_active and self._enabled and remaining:
                await self._restart_task()
            elif not remaining:
                self._enabled = False
                await self._cancel_task()
            return values, invalid, remaining

    async def replace_from_gate(self, start: bool = True) -> list[str]:
        """Apply gate changes made by a screen/load operation to the live feed."""
        async with self._lock:
            symbols = self.engine.gate.watchlist()
            if start and symbols:
                if self.running and tuple(symbols) == self._active_symbols:
                    return symbols
                self._enabled = True
                await self._restart_task()
            return symbols

    async def replace_automatic(
        self, symbols: list[str], start: bool = True
    ) -> list[str]:
        """Replace pre-screen-owned symbols while retaining manual choices."""
        values = {self.normalize(symbol) for symbol in symbols}
        async with self._lock:
            added = values - self._automatic
            stale = self._automatic - values - self._manual
            if stale:
                self.engine.gate.remove(*stale)
            self._automatic = values
            self._log.info(
                "event=automatic_watchlist_replaced added=%s removed=%s current=%s",
                ",".join(sorted(added)),
                ",".join(sorted(stale)),
                ",".join(sorted(values)),
            )
            if values:
                self.engine.gate.approve(*values)
            watchlist = self.engine.gate.watchlist()
            if start and watchlist:
                if self.running and tuple(watchlist) == self._active_symbols:
                    return watchlist
                self._enabled = True
                await self._restart_task()
            elif not watchlist:
                self._enabled = False
                await self._cancel_task()
            return watchlist

    async def _restart_task(self) -> None:
        await self._cancel_task()
        self._task = asyncio.create_task(self._supervise(), name=self._task_name)

    async def _cancel_task(self) -> None:
        if self._task is None:
            return
        task = self._task
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            self._log.error("watch task did not stop within 10 seconds")
        self._task = None
        self._active_symbols = ()

    async def _supervise(self) -> None:
        while self._enabled:
            symbols = self.engine.gate.watchlist()
            if not symbols:
                self._active_symbols = ()
                return
            self._active_symbols = tuple(symbols)
            self._log.info(
                "event=stream_attempt symbols=%s", ",".join(self._active_symbols)
            )
            try:
                await self.engine.watch(symbols)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("watch stream failed; retrying")
            if self._enabled:
                self._log.info(
                    "event=stream_retry delay_seconds=%s", self.retry_seconds
                )
                await asyncio.sleep(self.retry_seconds)
