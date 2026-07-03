"""Layer-1 alert rule: close below the lower Bollinger Band AND RSI oversold.

This is the only rule shipped publicly. The private strategy and its tuned
params live in the git-ignored rules/_private/ module and settings_local.py.
The rule is stateless: the engine owns per-symbol history and de-dup/cooldown.
"""

from .. import settings
from ..indicators import bollinger_bands, rsi
from ..interfaces import AlertRule
from ..models import Alert, Bar


class BBRSIRule(AlertRule):
    def __init__(
        self,
        bb_period: int = settings.BB_PERIOD,
        bb_std: float = settings.BB_STD,
        rsi_period: int = settings.RSI_PERIOD,
        rsi_threshold: float = settings.RSI_OVERSOLD,
    ) -> None:
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold

    @property
    def warmup(self) -> int:
        """Bars of 2-min history needed before the rule can evaluate."""
        return max(self.bb_period, self.rsi_period + 1)

    def evaluate(self, symbol: str, bars: list[Bar]) -> Alert | None:
        if len(bars) < self.warmup:
            return None

        closes = [b.close for b in bars]
        lower, _mid, _upper = bollinger_bands(closes, self.bb_period, self.bb_std)
        r = rsi(closes, self.rsi_period)
        last = bars[-1]

        if last.close < lower and r < self.rsi_threshold:
            return Alert(
                symbol=symbol,
                timestamp=last.timestamp,
                rule="bb_rsi_layer1",
                message=(
                    f"close {last.close:.2f} < lower BB {lower:.2f} "
                    f"and RSI {r:.1f} < {self.rsi_threshold:g}"
                ),
                context={"close": last.close, "bb_lower": lower, "rsi": r},
            )
        return None
