"""Read the curated watchlist (the pre-screen's input universe).

The file is a hand-maintained spreadsheet: a Ticker column plus an optional
List/category column. We only need the tickers (de-duplicated, upper-cased) and
each ticker's category label for the output. Reading is split out so the pure
parsing is unit-testable and the rest of the pipeline never sees pandas.
"""

import pandas as pd


def read_watchlist(path: str) -> list[tuple[str, str]]:
    """Return [(TICKER, category), ...], de-duplicated on ticker (first wins).

    Accepts .xls/.xlsx/.csv. Requires a "Ticker" column; a "List" column is used
    for the category if present, else "". Blank/NaN tickers are skipped.
    """
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)

    cols = {c.lower(): c for c in df.columns}
    if "ticker" not in cols:
        raise ValueError(
            f"watchlist {path!r} has no 'Ticker' column: {list(df.columns)}"
        )
    ticker_col = cols["ticker"]
    list_col = cols.get("list")

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        raw = row[ticker_col]
        if pd.isna(raw):
            continue
        sym = str(raw).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        category = ""
        if list_col is not None and not pd.isna(row[list_col]):
            category = str(row[list_col]).strip()
        out.append((sym, category))
    return out
