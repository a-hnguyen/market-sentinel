"""Core dataclasses shared across the engine."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Bar:
    symbol: str
    timestamp: datetime  # bar START time
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Candidate:
    symbol: str
    price: float
    pct_change: float  # day % change (negative for losers)
    volume_ratio: float  # volume / avg_volume
    market_cap: float
    pe: float | None = None
    ps: float | None = None
    near_52w_low: bool = False
    news_url: str | None = None
    source: str = ""  # "most_actives" | "day_losers"


@dataclass
class Alert:
    symbol: str
    timestamp: datetime
    rule: str  # e.g. "bb_rsi_layer1"
    message: str
    context: dict = field(default_factory=dict)  # close, bb_lower, rsi, etc.
