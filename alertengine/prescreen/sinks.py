"""Where the survivors go — the swappable output seam.

Today there's one sink: CsvSink, writing a file the user opens in Excel in the
morning. The seam exists so the eventual AWS path (EventBridge -> Lambda writing
rows to RDS/DynamoDB, archived to S3) is a new CandidateSink, not a rewrite of
the screener. The screener hands survivors to a sink and doesn't care what it is.
"""

import csv
from abc import ABC, abstractmethod

from .screener import ScreenResult


def load_candidates(path: str) -> list[str]:
    """Read back the survivor tickers a CsvSink wrote (the inverse of write).

    Used by the live engine at startup to auto-approve the pre-screen's output
    into the watchlist. Returns upper-cased tickers in file order; skips blank
    rows. Raises FileNotFoundError if the pre-screen hasn't run yet.
    """
    with open(path, newline="") as f:
        return [
            row["Ticker"].strip().upper()
            for row in csv.DictReader(f)
            if row.get("Ticker", "").strip()
        ]


class CandidateSink(ABC):
    @abstractmethod
    def write(self, results: list[ScreenResult]) -> None:
        """Persist tonight's survivors (overwriting any prior run)."""


class CsvSink(CandidateSink):
    """Overwrite a CSV each run. Opens in Excel on a double-click; also the
    natural bulk-load format for every AWS DB (CSV -> S3 -> load)."""

    def __init__(
        self, path: str, slow_label: str = "rsi_slow", fast_label: str = "rsi_fast"
    ) -> None:
        self.path = path
        self.slow_label = slow_label
        self.fast_label = fast_label

    def write(self, results: list[ScreenResult]) -> None:
        with open(self.path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["Ticker", self.slow_label, self.fast_label, "category", "scanned_at"]
            )
            for r in results:
                w.writerow(
                    [
                        r.symbol,
                        f"{r.rsi_slow:.1f}",
                        f"{r.rsi_fast:.1f}",
                        r.category,
                        r.scanned_at.isoformat(),
                    ]
                )
