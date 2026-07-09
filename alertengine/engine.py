"""Orchestrates screen -> approve -> watch -> alert.

The engine owns everything stateful: per-symbol 2-min bar history (from the
aggregator) and the de-dup/cooldown "armed" state, so the AlertRule can stay
stateless and swappable.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum

from . import settings
from .aggregator import BarAggregator
from .gate import ApprovalGate
from .interfaces import AlertRule, DataFeed, Notifier, Screener
from .models import Alert, Bar, Candidate


class Phase(Enum):
    """A watched symbol is always in exactly one of these."""

    WAITING = "waiting"  # watching for the layer-1 oversold setup
    ARMED = "armed"  # setup hit; hunting for 2 consecutive green closes
    COOLDOWN = "cooldown"  # just alerted/timed out; suppressed until setup clears


@dataclass
class _SymbolState:
    phase: Phase = Phase.WAITING
    consecutive_greens: int = 0  # progress toward the two-green confirmation
    bars_since_arm: int = 0  # timeout counter while ARMED
    bars_since_alert: int = 0  # min-floor counter while COOLDOWN
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
        confirm_green_bars: int = settings.CONFIRM_GREEN_BARS,
        arm_timeout_bars: int = settings.ARM_TIMEOUT_BARS,
        max_history: int = 200,
    ) -> None:
        self.screener = screener
        self.feed = feed
        self.rule = rule
        self.notifier = notifier
        self.gate = gate
        self.cooldown_bars = cooldown_bars
        self.confirm_green_bars = confirm_green_bars
        self.arm_timeout_bars = arm_timeout_bars
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
            await self._backfill(symbols)
            async for bar in self.feed.stream_bars(symbols):
                completed = self._agg.add(bar)
                if completed is not None:
                    await self._on_2min_bar(completed)
            # Drain any final in-progress buckets when the feed ends.
            for completed in self._agg.flush_all():
                await self._on_2min_bar(completed)
        finally:
            self.watching = False

    async def _backfill(self, symbols: list[str]) -> None:
        """Seed per-symbol 2-min history from recent REST bars so the rule has a
        full Bollinger/RSI window immediately on (re)start, instead of waiting
        ~40 min for live bars to accumulate. No-op unless the feed exposes
        `backfill_bars` (only the live Alpaca feed does; mock/replay don't need
        it). Crucially, seeded bars populate history only — the rule is NOT
        evaluated and no notifications fire on them.
        """
        provider = getattr(self.feed, "backfill_bars", None)
        if provider is None:
            return
        one_min_bars = await asyncio.to_thread(provider, symbols)
        if not one_min_bars:
            return
        # Fold 1-min -> 2-min through a throwaway aggregator so the live
        # aggregator (self._agg) starts clean: no partial historical bucket can
        # bleed into (and mis-complete on) the first live bar. The trailing
        # partial bucket is intentionally dropped — live bars supply the freshest
        # data, so only fully-formed 2-min bars seed the warm-up window.
        warm_agg = BarAggregator()
        seeded: dict[str, int] = {}
        for one_min in one_min_bars:
            completed = warm_agg.add(one_min)
            if completed is not None:
                self._seed_bar(completed, seeded)
        if seeded:
            total = sum(seeded.values())
            detail = ", ".join(f"{s}:{n}" for s, n in sorted(seeded.items()))
            print(f"backfill: seeded {total} 2-min bars for warm-up ({detail})")

    def _seed_bar(self, bar: Bar, seeded: dict[str, int]) -> None:
        state = self._states.setdefault(bar.symbol, _SymbolState())
        state.history.append(bar)
        if len(state.history) > self.max_history:
            del state.history[: -self.max_history]
        seeded[bar.symbol] = seeded.get(bar.symbol, 0) + 1

    async def _on_2min_bar(self, bar: Bar) -> None:
        state = self._states.setdefault(bar.symbol, _SymbolState())
        state.bars_seen += 1
        state.history.append(bar)
        if len(state.history) > self.max_history:
            del state.history[: -self.max_history]

        # Layer-1 (close < lower BB AND RSI < 30) is the oversold "setup". It no
        # longer fires the final alert directly — it only *arms* a symbol.
        setup = self.rule.evaluate(bar.symbol, state.history)
        oversold = setup is not None

        if state.phase is Phase.WAITING:
            if oversold:
                await self._arm(state, bar, setup)
        elif state.phase is Phase.ARMED:
            await self._advance_armed(state, bar)
        elif state.phase is Phase.COOLDOWN:
            self._advance_cooldown(state, oversold)

    async def _arm(self, state: _SymbolState, bar: Bar, setup: Alert) -> None:
        """WAITING -> ARMED: fire a console-only WATCH and start the two-green
        hunt. The arming bar itself does NOT count toward the greens."""
        setup.kind = "watch"
        self._enrich(setup, bar.symbol)
        await self.notifier.send(setup)
        state.phase = Phase.ARMED
        state.consecutive_greens = 0
        state.bars_since_arm = 0

    async def _advance_armed(self, state: _SymbolState, bar: Bar) -> None:
        state.bars_since_arm += 1
        if bar.close > bar.open:  # green close
            state.consecutive_greens += 1
        else:  # any red close breaks the streak (must be *consecutive*)
            state.consecutive_greens = 0
        # Success beats timeout: check the confirmation before the clock.
        if state.consecutive_greens >= self.confirm_green_bars:
            await self._fire_buy(state, bar)
        elif state.bars_since_arm >= self.arm_timeout_bars:
            self._timeout(state)

    async def _fire_buy(self, state: _SymbolState, bar: Bar) -> None:
        """ARMED -> COOLDOWN: the setup confirmed. This is the phone-pushed one."""
        alert = Alert(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            rule="bb_rsi_buy",
            message=(
                f"BUY {bar.symbol}: {self.confirm_green_bars} green 2-min closes "
                f"confirmed after oversold arm (close {bar.close:.2f})"
            ),
            context={"close": bar.close},
            kind="buy",
        )
        self._enrich(alert, bar.symbol)
        await self.notifier.send(alert)
        state.phase = Phase.COOLDOWN
        state.bars_since_alert = 0

    def _timeout(self, state: _SymbolState) -> None:
        """ARMED -> WAITING: no confirmation in the window. Drop *all* collected
        state for the symbol (bar history included) and start over."""
        state.phase = Phase.WAITING
        state.consecutive_greens = 0
        state.bars_since_arm = 0
        state.history.clear()

    def _advance_cooldown(self, state: _SymbolState, oversold: bool) -> None:
        """COOLDOWN -> WAITING once the setup has cleared AND a min floor of bars
        has elapsed, so we never re-fire on the same continuous oversold episode.
        """
        state.bars_since_alert += 1
        if not oversold and state.bars_since_alert >= self.cooldown_bars:
            state.phase = Phase.WAITING
            state.consecutive_greens = 0

    def _enrich(self, alert: Alert, symbol: str) -> None:
        """Attach the day's % change / relative volume from screening, so the
        notifier can show them. Keeps the rule stateless/bar-only."""
        cand = self._candidates.get(symbol)
        if cand is not None:
            alert.context.setdefault("pct_change", cand.pct_change)
            alert.context.setdefault("volume_ratio", cand.volume_ratio)

    def status(self) -> dict:
        return {
            "watching": self.watching,
            "symbols": {
                sym: {
                    "bars_seen": st.bars_seen,
                    "phase": st.phase.value,
                    "greens": st.consecutive_greens,
                    "history": len(st.history),
                }
                for sym, st in self._states.items()
            },
        }
