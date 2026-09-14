"""Runtime-selectable alert strategy configuration."""

from dataclasses import dataclass

from .interfaces import AlertRule, ArmedTriggerRule, ConfirmationRule


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    rule: AlertRule
    exit_rule: AlertRule | None = None
    buy_confirmation_rule: ConfirmationRule | None = None
    buy_trigger_rule: ArmedTriggerRule | None = None
    sell_trigger_rule: ArmedTriggerRule | None = None
    buy_fire_rule: str = "bb_rsi_buy"
    sell_fire_rule: str = "bb_rsi_sell"
    bar_interval_minutes: int = 2
    arm_timeout_bars: int = 15
    preserve_history_on_timeout: bool = False
    repeat_watch_lifecycle: bool = False

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip().lower():
            raise ValueError("strategy name must be non-empty, lowercase, and trimmed")
        if self.bar_interval_minutes < 1:
            raise ValueError("bar interval must be positive")
        if self.arm_timeout_bars < 1:
            raise ValueError("arm timeout must be positive")
