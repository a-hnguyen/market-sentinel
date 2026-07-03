"""Hardcoded screener for testing the approve/watch flow with no network."""

from ..interfaces import Screener
from ..models import Candidate


class MockScreener(Screener):
    def __init__(self, candidates: list[Candidate] | None = None) -> None:
        self._candidates = candidates or [
            Candidate(
                symbol="MOCK",
                price=50.0,
                pct_change=-16.2,
                volume_ratio=2.4,
                market_cap=3_500_000_000,
                pe=18.0,
                ps=4.2,
                near_52w_low=True,
                source="day_losers",
            ),
            Candidate(
                symbol="TESTA",
                price=42.0,
                pct_change=-8.1,
                volume_ratio=1.3,
                market_cap=8_000_000_000,
                source="most_actives",
            ),
        ]

    async def get_candidates(self) -> list[Candidate]:
        return list(self._candidates)
