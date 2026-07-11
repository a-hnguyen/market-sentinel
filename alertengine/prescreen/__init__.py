"""Overnight (swing) pre-screen.

An off-hours batch job, separate from the live intraday engine. It reads a
curated watchlist, runs an RSI-only oversold confluence across two timeframes
(slow + fast) over Alpaca historical bars, and hands the survivors to a sink
(CSV today; a DB/S3 sink later — see the EventBridge->Lambda edge in the AWS
plan). The survivors are the next session's watch candidates: they flow into the
same ApprovalGate -> intraday BuyAlert path as the live screen's candidates.

Nothing here touches the state machine; it only produces a watchlist.
"""

from .screener import PreScreener, ScreenResult, evaluate_confluence
from .sinks import CandidateSink, CsvSink, load_candidates
from .watchlist import read_watchlist

__all__ = [
    "PreScreener",
    "ScreenResult",
    "evaluate_confluence",
    "CandidateSink",
    "CsvSink",
    "load_candidates",
    "read_watchlist",
]
