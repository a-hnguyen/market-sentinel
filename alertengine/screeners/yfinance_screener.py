"""Real screener over Yahoo Finance predefined lists + configurable per-ticker filters.

yfinance is used for **screening only**, never the trade/data path (bars come
from the DataFeed). Screening is infrequent and non-latency-sensitive, so a
delayed or occasionally-flaky list is acceptable here; on failure we fall back
to the last good result rather than returning an empty universe.

Fetch/filter are split so the (pure) filter logic is unit-testable without
network. Thresholds default to config but are injectable for tests.
"""

import asyncio

import yfinance as yf

from .. import settings
from ..interfaces import Screener
from ..models import Candidate


class YFinanceScreener(Screener):
    def __init__(
        self,
        sources: tuple[str, ...] = ("most_actives", "day_losers"),
        price_min: float = settings.PRICE_MIN,
        price_max: float = settings.PRICE_MAX,
        min_market_cap: float = settings.MIN_MARKET_CAP,
        loser_min_volume_ratio: float = settings.LOSER_MIN_VOLUME_RATIO,
        loser_min_pct_loss: float = settings.LOSER_MIN_PCT_LOSS,
        near_low_pct: float = 0.10,  # within 10% of the 52-week low
    ) -> None:
        self.sources = sources
        self.price_min = price_min
        self.price_max = price_max
        self.min_market_cap = min_market_cap
        self.loser_min_volume_ratio = loser_min_volume_ratio
        self.loser_min_pct_loss = loser_min_pct_loss
        self.near_low_pct = near_low_pct
        self._last_good: list[Candidate] = []

    async def get_candidates(self) -> list[Candidate]:
        # yf.screen is blocking network I/O; keep the event loop free.
        try:
            raw = await asyncio.to_thread(self._fetch)
        except Exception:
            # Yahoo hiccup -> serve the last good universe, never an empty one.
            return list(self._last_good)

        candidates = [c for c in (self._to_candidate(q) for q in raw) if c]
        filtered = [c for c in candidates if self._passes(c)]
        if filtered:
            self._last_good = filtered
        return filtered

    def _fetch(self) -> list[dict]:
        quotes: list[dict] = []
        for src in self.sources:
            result = yf.screen(src)
            for q in result.get("quotes", []):
                q = dict(q)
                q["_source"] = src
                quotes.append(q)
        return quotes

    def _to_candidate(self, q: dict) -> Candidate | None:
        symbol = q.get("symbol")
        price = q.get("regularMarketPrice")
        if not symbol or price is None:
            return None
        avg_vol = q.get("averageDailyVolume3Month") or 0
        vol = q.get("regularMarketVolume") or 0
        low_52w = q.get("fiftyTwoWeekLow")
        return Candidate(
            symbol=symbol,
            price=float(price),
            pct_change=float(q.get("regularMarketChangePercent") or 0.0),
            volume_ratio=(vol / avg_vol) if avg_vol else 0.0,
            market_cap=float(q.get("marketCap") or 0),
            pe=q.get("trailingPE"),
            ps=q.get("priceToSales"),
            near_52w_low=(
                low_52w is not None and price <= low_52w * (1 + self.near_low_pct)
            ),
            source=q.get("_source", ""),
        )

    def _passes(self, c: Candidate) -> bool:
        if not (self.price_min <= c.price <= self.price_max):
            return False
        if c.market_cap < self.min_market_cap:
            return False
        # Losers get an extra volume + magnitude-of-drop gate.
        if c.source == "day_losers":
            if c.volume_ratio < self.loser_min_volume_ratio:
                return False
            if c.pct_change > -self.loser_min_pct_loss:
                return False
        return True
