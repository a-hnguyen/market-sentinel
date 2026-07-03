"""ApprovalGate: the human-in-the-loop seam between screening and watching.

In v1 this is just an approved-symbol set the user manages via the REPL, but it
is a first-class pipeline stage on purpose: later phases (suggested action,
then automated-with-limits) move the human checkpoint without an architecture
rewrite.
"""


class ApprovalGate:
    def __init__(self) -> None:
        self._approved: set[str] = set()

    def approve(self, *symbols: str) -> None:
        for s in symbols:
            self._approved.add(s.upper())

    def remove(self, *symbols: str) -> None:
        for s in symbols:
            self._approved.discard(s.upper())

    def watchlist(self) -> list[str]:
        return sorted(self._approved)

    def __contains__(self, symbol: str) -> bool:
        return symbol.upper() in self._approved
