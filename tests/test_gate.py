"""ApprovalGate: the approved-symbol set behind the human-in-the-loop seam."""

from alertengine.gate import ApprovalGate


def test_approve_and_watchlist_is_sorted_and_upper():
    g = ApprovalGate()
    g.approve("tsla", "aapl")
    assert g.watchlist() == ["AAPL", "TSLA"]  # upper-cased, sorted


def test_approve_is_idempotent():
    g = ApprovalGate()
    g.approve("AAPL")
    g.approve("aapl")  # same symbol, different case
    assert g.watchlist() == ["AAPL"]


def test_remove():
    g = ApprovalGate()
    g.approve("AAPL", "TSLA")
    g.remove("aapl")  # case-insensitive
    assert g.watchlist() == ["TSLA"]


def test_remove_unknown_is_noop():
    g = ApprovalGate()
    g.approve("AAPL")
    g.remove("NVDA")  # not present -> no error, no change
    assert g.watchlist() == ["AAPL"]


def test_contains_is_case_insensitive():
    g = ApprovalGate()
    g.approve("AAPL")
    assert "aapl" in g
    assert "AAPL" in g
    assert "tsla" not in g


def test_starts_empty():
    assert ApprovalGate().watchlist() == []
