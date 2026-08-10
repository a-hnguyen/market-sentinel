"""Read-only Robinhood MCP historical-bar adapter.

Robinhood exposes native 1-minute ``24_5`` bars but no native 2-minute
interval. This adapter normalizes those 1-minute responses into the engine's
``Bar`` model; ``BarAggregator`` remains responsible for constructing the
clock-aligned 2-minute candles used by the strategy.

The MCP transport is injected deliberately. OAuth/token storage and the actual
MCP client belong at the deployment boundary, while parsing, batching, and bar
semantics stay deterministic and unit-testable here.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

from ..interfaces import HistoricalBarFeed
from ..models import Bar

_BATCH_SIZE = 10  # Robinhood get_equity_historicals limit


class RobinhoodHistoricalTransport(Protocol):
    """Minimal allowlisted MCP capability required by the alert service."""

    async def get_equity_historicals(
        self, request: Mapping[str, object]
    ) -> Mapping[str, Any]: ...


class RobinhoodHistoricalFeed(HistoricalBarFeed):
    """Fetch and normalize split-adjusted 1-minute 24/5 equity bars."""

    def __init__(self, transport: RobinhoodHistoricalTransport) -> None:
        self._transport = transport

    @staticmethod
    def _batches(symbols: list[str]) -> list[list[str]]:
        normalized = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
        if any(not symbol for symbol in normalized):
            raise ValueError("symbols must not be blank")
        return [
            normalized[index : index + _BATCH_SIZE]
            for index in range(0, len(normalized), _BATCH_SIZE)
        ]

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start and end must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError(f"Robinhood returned a naive timestamp: {value!r}")
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _parse_bar(cls, symbol: str, payload: Mapping[str, Any]) -> Bar:
        return Bar(
            symbol=symbol,
            timestamp=cls._timestamp(str(payload["begins_at"])),
            open=float(payload["open_price"]),
            high=float(payload["high_price"]),
            low=float(payload["low_price"]),
            close=float(payload["close_price"]),
            volume=float(payload["volume"]),
            interpolated=bool(payload.get("interpolated", False)),
            session=str(payload["session"]) if payload.get("session") else None,
        )

    async def fetch_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> list[Bar]:
        start_utc = self._utc(start)
        end_utc = self._utc(end)
        if start_utc >= end_utc:
            raise ValueError("start must be before end")

        bars: list[Bar] = []
        for batch in self._batches(symbols):
            response = await self._transport.get_equity_historicals(
                {
                    "symbols": batch,
                    "start_time": start_utc.isoformat().replace("+00:00", "Z"),
                    "end_time": end_utc.isoformat().replace("+00:00", "Z"),
                    "interval": "minute",
                    "bounds": "24_5",
                    "adjustment_type": "split",
                }
            )
            data = response.get("data")
            if not isinstance(data, Mapping):
                raise ValueError("Robinhood response is missing data")
            results = data.get("results", [])
            if not isinstance(results, list):
                raise ValueError("Robinhood response results must be a list")
            for result in results:
                if not isinstance(result, Mapping):
                    raise ValueError("Robinhood result must be an object")
                symbol = str(result["symbol"]).upper()
                payloads = result.get("bars", [])
                if not isinstance(payloads, list):
                    raise ValueError("Robinhood bars must be a list")
                bars.extend(self._parse_bar(symbol, payload) for payload in payloads)

        # MCP batches are returned independently. Merge them into the same
        # chronological ordering a live multi-symbol stream would produce.
        bars.sort(key=lambda bar: (bar.timestamp, bar.symbol))
        return bars
