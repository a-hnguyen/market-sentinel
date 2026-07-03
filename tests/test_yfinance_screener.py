"""Unit tests for the yfinance screener's pure fetch->candidate->filter logic.

No network: we exercise `_to_candidate` / `_passes` directly with fake Yahoo
quote dicts, so the filter criteria are tested deterministically.
"""

from alertengine.screeners.yfinance_screener import YFinanceScreener


def quote(**kw) -> dict:
    base = {
        "symbol": "AAA",
        "regularMarketPrice": 40.0,
        "regularMarketChangePercent": -16.0,
        "regularMarketVolume": 2_000_000,
        "averageDailyVolume3Month": 1_000_000,
        "marketCap": 3_000_000_000,
        "fiftyTwoWeekLow": 38.0,
        "_source": "day_losers",
    }
    base.update(kw)
    return base


def make(**thresholds) -> YFinanceScreener:
    # Arbitrary generic thresholds — enough to exercise the filter logic; NOT the
    # real screening criteria (those live in git-ignored settings_local.py).
    defaults = dict(
        price_min=10,
        price_max=100,
        min_market_cap=1_000_000_000,
        loser_min_volume_ratio=1.5,
        loser_min_pct_loss=10.0,
    )
    defaults.update(thresholds)
    return YFinanceScreener(**defaults)


def test_to_candidate_maps_fields_and_ratios():
    s = make()
    c = s._to_candidate(quote())
    assert c.symbol == "AAA"
    assert c.price == 40.0
    assert c.volume_ratio == 2.0  # 2,000,000 / 1,000,000
    assert c.near_52w_low is True  # 40 <= 38 * 1.10
    assert c.source == "day_losers"


def test_to_candidate_skips_missing_price_or_symbol():
    s = make()
    assert s._to_candidate({"symbol": "X"}) is None
    assert s._to_candidate({"regularMarketPrice": 10}) is None


def test_to_candidate_handles_zero_avg_volume():
    s = make()
    c = s._to_candidate(quote(averageDailyVolume3Month=0))
    assert c.volume_ratio == 0.0  # no divide-by-zero


def test_loser_passes_all_gates():
    assert make()._passes(make()._to_candidate(quote())) is True


def test_loser_rejected_when_drop_too_small():
    s = make()
    # Only -5% loss, threshold is 10%.
    assert s._passes(s._to_candidate(quote(regularMarketChangePercent=-5.0))) is False


def test_loser_rejected_when_volume_ratio_too_low():
    s = make()
    assert s._passes(s._to_candidate(quote(regularMarketVolume=1_000_000))) is False  # ratio 1.0


def test_price_band_enforced():
    s = make()
    assert s._passes(s._to_candidate(quote(regularMarketPrice=150.0))) is False
    assert s._passes(s._to_candidate(quote(regularMarketPrice=5.0))) is False


def test_market_cap_floor_enforced():
    s = make()
    assert s._passes(s._to_candidate(quote(marketCap=500_000_000))) is False


def test_most_actives_skips_loser_only_gates():
    """most_actives candidates are not subject to the volume/drop gates."""
    s = make()
    # Small drop + low volume, but source is most_actives -> only price/cap apply.
    c = s._to_candidate(
        quote(_source="most_actives", regularMarketChangePercent=-1.0,
              regularMarketVolume=100_000)
    )
    assert s._passes(c) is True
