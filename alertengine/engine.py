"""Orchestrates screen -> approve -> watch -> alert.

The engine owns everything stateful: per-symbol 2-min bar history (from the
aggregator) and the de-dup/cooldown "armed" state, so the AlertRule can stay
stateless and swappable.
"""

from dataclasses import dataclass, field

from . import settings
from .aggregator import BarAggregator
from .gate import ApprovalGate
from .interfaces import AlertRule, DataFeed, Notifier, Screener
from .models import Bar, Candidate


@dataclass
class _SymbolState:
    armed: bool = True  # ready to fire an alert
    bars_since_fire: int = 0
    bars_seen: int = 0
    history: list[Bar] = field(default_factory=list)


class AlertEngine:
    def __init__(
        self,
        screener: Screener,
        feed: DataFeed,
        rule: AlertRule,
        notifier: Notifier,
        gate: ApprovalGate,
        cooldown_bars: int = settings.COOLDOWN_BARS,
        max_history: int = 200,
    ) -> None:
        self.screener = screener
        self.feed = feed
        self.rule = rule
        self.notifier = notifier
        self.gate = gate
        self.cooldown_bars = cooldown_bars
        self.max_history = max_history

        self._agg = BarAggregator()
        self._states: dict[str, _SymbolState] = {}
        # Latest screened candidate per symbol, so alerts can carry the day's
        # % change / relative volume (which live on the Candidate, not the bars).
        self._candidates: dict[str, Candidate] = {}
        self.watching = False

    async def screen(self) -> list[Candidate]:
        candidates = await self.screener.get_candidates()
        self._candidates = {c.symbol: c for c in candidates}
        return candidates

    async def watch(self, symbols: list[str]) -> None:
        """Consume 1-min bars for `symbols`, aggregate to 2-min, evaluate the
        rule, and notify (with de-dup). Runs until the feed ends or is cancelled.
        """
        self.watching = True
        try:
            async for bar in self.feed.stream_bars(symbols):
                completed = self._agg.add(bar)
                if completed is not None:
                    await self._on_2min_bar(completed)
            # Drain any final in-progress buckets when the feed ends.
            for completed in self._agg.flush_all():
                await self._on_2min_bar(completed)
        finally:
            self.watching = False

    async def _on_2min_bar(self, bar: Bar) -> None:
        state = self._states.setdefault(bar.symbol, _SymbolState())
        state.bars_seen += 1
        state.history.append(bar)
        if len(state.history) > self.max_history:
            del state.history[: -self.max_history]

        alert = self.rule.evaluate(bar.symbol, state.history)

        if alert is not None:
            # Enrich with screening context (day % change, relative volume) so
            # the notifier can show it. Keeps the rule stateless/bar-only.
            cand = self._candidates.get(bar.symbol)
            if cand is not None:
                alert.context.setdefault("pct_change", cand.pct_change)
                alert.context.setdefault("volume_ratio", cand.volume_ratio)
            if state.armed:
                await self.notifier.send(alert)
                state.armed = False
                state.bars_since_fire = 0
            else:
                # Still firing but muted; re-arm only after the cooldown.
                state.bars_since_fire += 1
                if state.bars_since_fire >= self.cooldown_bars:
                    state.armed = True
                    state.bars_since_fire = 0
        else:
            # Condition cleared -> re-arm immediately.
            state.armed = True
            state.bars_since_fire = 0

    def status(self) -> dict:
        return {
            "watching": self.watching,
            "symbols": {
                sym: {
                    "bars_seen": st.bars_seen,
                    "armed": st.armed,
                    "history": len(st.history),
                }
                for sym, st in self._states.items()
            },
        }
