"""Orchestrates screen -> approve -> watch -> alert.

The engine owns everything stateful: per-symbol 2-min bar history (from the
aggregator) and the de-dup/cooldown "armed" state, so the AlertRule can stay
stateless and swappable.

Each symbol runs one or two identical confirmation machines over its shared bar
history: a **long** machine (oversold setup -> two green closes -> BUY) and,
when an exit rule is supplied, an independent **short** machine (overbought
setup -> two red closes -> SELL). They are mirror images — same arm/confirm/
timeout/cooldown transitions, only the setup rule and the confirming-close
direction differ.
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
    """A confirmation machine is always in exactly one of these."""

    WAITING = "waiting"  # watching for the setup
    ARMED = "armed"  # setup hit; hunting for 2 consecutive confirming closes
    COOLDOWN = "cooldown"  # just alerted/timed out; suppressed until setup clears


@dataclass
class _DirectionMachine:
    """One arm->confirm->timeout->cooldown state machine for a single direction.

    `long=True` hunts green closes after an oversold setup (BUY); `long=False`
    hunts red closes after an overbought setup (SELL). Everything else — the
    transitions, timeout, and cooldown — is identical.
    """

    confirm_bars: int
    arm_timeout_bars: int
    cooldown_bars: int
    long: bool
    phase: Phase = Phase.WAITING
    consecutive: int = 0  # progress toward the two-close confirmation
    bars_since_arm: int = 0  # timeout counter while ARMED
    bars_since_alert: int = 0  # min-floor counter while COOLDOWN

    def is_confirm_close(self, bar: Bar) -> bool:
        """A confirming close: green (up) for long, red (down) for short."""
        return bar.close > bar.open if self.long else bar.close < bar.open

    @property
    def watch_kind(self) -> str:
        return "watch" if self.long else "sell_watch"

    @property
    def fire_kind(self) -> str:
        return "buy" if self.long else "sell"

    @property
    def fire_rule(self) -> str:
        return "bb_rsi_buy" if self.long else "bb_rsi_sell"


@dataclass
class _SymbolState:
    long: _DirectionMachine
    short: _DirectionMachine | None = None  # None when no exit rule is wired
    bars_seen: int = 0
    history: list[Bar] = field(default_factory=list)

    def machines(self) -> list[_DirectionMachine]:
        return [self.long] if self.short is None else [self.long, self.short]


class AlertEngine:
    def __init__(
        self,
        screener: Screener,
        feed: DataFeed,
        rule: AlertRule,
        notifier: Notifier,
        gate: ApprovalGate,
        exit_rule: AlertRule | None = None,
        cooldown_bars: int = settings.COOLDOWN_BARS,
        confirm_green_bars: int = settings.CONFIRM_GREEN_BARS,
        confirm_red_bars: int = settings.CONFIRM_RED_BARS,
        arm_timeout_bars: int = settings.ARM_TIMEOUT_BARS,
        max_history: int = 200,
    ) -> None:
        self.screener = screener
        self.feed = feed
        self.rule = rule
        self.exit_rule = exit_rule
        self.notifier = notifier
        self.gate = gate
        self.cooldown_bars = cooldown_bars
        self.confirm_green_bars = confirm_green_bars
        self.confirm_red_bars = confirm_red_bars
        self.arm_timeout_bars = arm_timeout_bars
        self.max_history = max_history

        self._agg = BarAggregator()
        self._states: dict[str, _SymbolState] = {}
        # Latest screened candidate per symbol, so alerts can carry the day's
        # % change / relative volume (which live on the Candidate, not the bars).
        self._candidates: dict[str, Candidate] = {}
        self.watching = False

    def _new_state(self) -> _SymbolState:
        """Build a symbol's machines from the engine's tunables. The short (SELL)
        machine only exists when an exit rule is wired."""
        long = _DirectionMachine(
            confirm_bars=self.confirm_green_bars,
            arm_timeout_bars=self.arm_timeout_bars,
            cooldown_bars=self.cooldown_bars,
            long=True,
        )
        short = None
        if self.exit_rule is not None:
            short = _DirectionMachine(
                confirm_bars=self.confirm_red_bars,
                arm_timeout_bars=self.arm_timeout_bars,
                cooldown_bars=self.cooldown_bars,
                long=False,
            )
        return _SymbolState(long=long, short=short)

    async def screen(self) -> list[Candidate]:
        candidates = await self.screener.get_candidates()
        self._candidates = {c.symbol: c for c in candidates}
        return candidates

    async def watch(self, symbols: list[str]) -> None:
        """Consume 1-min bars for `symbols`, aggregate to 2-min, evaluate the
        rule, and notify (with de-dup). Runs until the feed ends or is cancelled.
        """
        # A WatchController may intentionally restart the stream when the remote
        # watchlist changes. Never carry a half-built bucket across subscriptions.
        self._agg = BarAggregator()
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
        state = self._states.setdefault(bar.symbol, self._new_state())
        state.history.append(bar)
        if len(state.history) > self.max_history:
            del state.history[: -self.max_history]
        seeded[bar.symbol] = seeded.get(bar.symbol, 0) + 1

    async def _on_2min_bar(self, bar: Bar) -> None:
        state = self._states.setdefault(bar.symbol, self._new_state())
        state.bars_seen += 1
        state.history.append(bar)
        if len(state.history) > self.max_history:
            del state.history[: -self.max_history]

        # The setup rules only *arm* their machine — the final alert fires on the
        # two-close confirmation, not here. Evaluate long always, short only when
        # an exit rule is wired.
        long_setup = self.rule.evaluate(bar.symbol, state.history)
        await self._step(state, state.long, bar, long_setup)

        if state.short is not None:
            short_setup = self.exit_rule.evaluate(bar.symbol, state.history)
            await self._step(state, state.short, bar, short_setup)

    async def _step(
        self, state: _SymbolState, m: _DirectionMachine, bar: Bar, setup: Alert | None
    ) -> None:
        signal = setup is not None
        if m.phase is Phase.WAITING:
            if signal:
                await self._arm(m, bar, setup)
        elif m.phase is Phase.ARMED:
            await self._advance_armed(state, m, bar)
        elif m.phase is Phase.COOLDOWN:
            self._advance_cooldown(m, signal)

    async def _arm(self, m: _DirectionMachine, bar: Bar, setup: Alert) -> None:
        """WAITING -> ARMED: fire a WATCH alert and start the two-close
        hunt. The arming bar itself does NOT count toward the confirmation."""
        setup.kind = m.watch_kind
        self._enrich(setup, bar.symbol)
        await self.notifier.send(setup)
        m.phase = Phase.ARMED
        m.consecutive = 0
        m.bars_since_arm = 0

    async def _advance_armed(
        self, state: _SymbolState, m: _DirectionMachine, bar: Bar
    ) -> None:
        m.bars_since_arm += 1
        if m.is_confirm_close(bar):
            m.consecutive += 1
        else:  # a non-confirming close breaks the streak (must be *consecutive*)
            m.consecutive = 0
        # Success beats timeout: check the confirmation before the clock.
        if m.consecutive >= m.confirm_bars:
            await self._fire(m, bar)
        elif m.bars_since_arm >= m.arm_timeout_bars:
            self._timeout(state, m)

    async def _fire(self, m: _DirectionMachine, bar: Bar) -> None:
        """ARMED -> COOLDOWN: the setup confirmed; fire BUY/SELL."""
        if m.long:
            message = (
                f"BUY {bar.symbol}: {m.confirm_bars} green 2-min closes "
                f"confirmed after oversold arm (close {bar.close:.2f})"
            )
        else:
            message = (
                f"SELL {bar.symbol}: {m.confirm_bars} red 2-min closes "
                f"confirmed after overbought arm (close {bar.close:.2f})"
            )
        alert = Alert(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            rule=m.fire_rule,
            message=message,
            context={"close": bar.close},
            kind=m.fire_kind,
        )
        self._enrich(alert, bar.symbol)
        await self.notifier.send(alert)
        m.phase = Phase.COOLDOWN
        m.bars_since_alert = 0

    def _timeout(self, state: _SymbolState, m: _DirectionMachine) -> None:
        """ARMED -> WAITING: no confirmation in the window. Reset this machine.

        Bar history is shared across a symbol's machines, so it's only dropped
        when *every other* machine is idle (WAITING) — otherwise a timeout on one
        direction would blind the other's Bollinger/RSI window. With no exit rule
        wired there is no peer, so history is always dropped (the original
        single-machine behavior).
        """
        m.phase = Phase.WAITING
        m.consecutive = 0
        m.bars_since_arm = 0
        if all(
            other is m or other.phase is Phase.WAITING for other in state.machines()
        ):
            state.history.clear()

    def _advance_cooldown(self, m: _DirectionMachine, signal: bool) -> None:
        """COOLDOWN -> WAITING once the setup has cleared AND a min floor of bars
        has elapsed, so we never re-fire on the same continuous setup episode.
        """
        m.bars_since_alert += 1
        if not signal and m.bars_since_alert >= m.cooldown_bars:
            m.phase = Phase.WAITING
            m.consecutive = 0

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
                    "phase": st.long.phase.value,
                    "greens": st.long.consecutive,
                    "sell_phase": st.short.phase.value if st.short else "disabled",
                    "reds": st.short.consecutive if st.short else 0,
                    "history": len(st.history),
                }
                for sym, st in self._states.items()
            },
        }
