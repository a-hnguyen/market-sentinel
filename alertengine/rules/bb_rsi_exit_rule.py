"""Exit rule: close above the upper Bollinger Band AND RSI overbought.

The public textbook mirror of `bb_rsi_rule.py` for the sell side. Like the buy
rule, this is the only exit logic shipped publicly; the private strategy's real
exit refinements live in the git-ignored rules/_private/ module and
settings_local.py. Stateless: the engine owns per-symbol history and the
arm/confirm/cooldown state.
"""

from .. import settings
from ..indicators import bollinger_bands, rsi
from ..interfaces import AlertRule
from ..models import Alert, Bar


class BBRSIExitRule(AlertRule):
    def __init__(
        self,
        bb_period: int = settings.BB_PERIOD,
        bb_std: float = settings.BB_STD,
        rsi_period: int = settings.RSI_PERIOD,
        rsi_threshold: float = settings.RSI_OVERBOUGHT,
    ) -> None:
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold

    @property
    def warmup(self) -> int:
        """Bars of 2-min history needed to evaluate."""
        return max(self.bb_period, self.rsi_period + 1)

    def evaluate(self, symbol: str, bars: list[Bar]) -> Alert | None:
        if len(bars) < self.warmup:
            return None

        closes = [b.close for b in bars]
        _lower, _mid, upper = bollinger_bands(closes, self.bb_period, self.bb_std)
        r = rsi(closes, self.rsi_period)
        last = bars[-1]

        if last.close > upper and r > self.rsi_threshold:
            return Alert(
                symbol=symbol,
                timestamp=last.timestamp,
                rule="bb_rsi_exit",
                message=(
                    f"close {last.close:.2f} > upper BB {upper:.2f} "
                    f"and RSI {r:.1f} > {self.rsi_threshold:g}"
                ),
                context={"close": last.close, "bb_upper": upper, "rsi": r},
            )
        return None
