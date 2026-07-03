# CLAUDE.md — market-sentinel

Async Python **alert engine** (not an auto-trader). It screens a stock universe
on demand, watches human-approved symbols on 2-min bars, and fires a console
alert when the layer-1 setup hits: **close < lower Bollinger Band AND RSI < 30**.
A human triggers screening and approves the watchlist. **No orders are ever
placed.** See `BUILD_SPEC.md` for the full v1 build spec (git-ignored — contains
the private strategy IP).

## Non-negotiable rules

- **IP boundary — never commit private strategy.** The repo is intended to go
  public, so only the public layer-1 rule (`alertengine/rules/bb_rsi_rule.py`)
  and mocks ship. The private strategy logic goes in git-ignored
  `alertengine/rules/_private/`; real tuned params/criteria go in git-ignored
  `alertengine/settings_local.py` (which overrides `settings.py`). Hidden logic
  must be **ABSENT, not obfuscated**. `.gitignore` must keep covering `.env`,
  `rules/_private/`, `settings_local.py`, `*.log`, `BUILD_SPEC.md`,
  `Trading_Bot_Context.md`, `JS_Context.md`, and `*.pdf` before any commit.
- **yfinance is screening ONLY, never the trade/data path.** Bars come from the
  DataFeed (mock now, Alpaca later). Keep the two sources separate.
- **The four seams are load-bearing** — `Screener`, `DataFeed`, `AlertRule`,
  `Notifier` (`alertengine/interfaces.py`) plus the `ApprovalGate`. Don't merge
  or rename them to "simplify"; swapping mock→real (Alpaca/yfinance) and adding a
  dashboard/IBKR later depends on these boundaries staying intact.
- **Build incrementally, follow BUILD_SPEC's Build Order.** Don't one-shot the app.
  The aggregator is the one place a silent bug poisons everything downstream —
  its test must pass before building on top.

## Architecture

```
Screener ─▶ [ApprovalGate] ─▶ DataFeed(1-min) ─▶ aggregator(2-min)
                                                        │
                                              indicators (BB, RSI)
                                                        │
                                          AlertRule (bb_rsi_layer1)
                                                        │
                                    engine de-dup/cooldown ─▶ Notifier
```

- `alertengine/aggregator.py` — folds 1-min → clock-aligned 2-min bars
  (flush-on-advance; handles IEX missing-minute). Alpaca has no native 2-min
  stream, hence the aggregation.
- `alertengine/engine.py` — owns all state: per-symbol 2-min history +
  armed/cooldown de-dup. Keeps `AlertRule` stateless.
- `alertengine/settings.py` — **all tunables live here** (indicator params, screen
  filters, cooldown), as generic publishable placeholders. Real confirmed values
  live in git-ignored `settings_local.py`, which overrides them at import time —
  so confirming a number is a one-line edit in the private override, never a code
  change to tracked files. Imported as `from alertengine import settings` — keep
  it inside the package (do not move it back to a root-level `config/`, which
  breaks imports from other cwds).

## Commands

Use the project venv (`.venv`), never the `tesorai` env:

```bash
source .venv/bin/activate
pytest tests/ -q                   # run all tests (no plugins needed)
python -m alertengine              # mock mode — synthetic bars, no API keys
python -m alertengine --live       # live — yfinance screen + Alpaca 1-min feed
# REPL: screen → approve <SYMS> → watch → status → quit
```

Live mode needs `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` in a `.env` (copy
`.env.example`). Mock<->live only swaps which Screener/DataFeed `__main__`
builds; the engine is identical.

- Tests use `asyncio.run(...)`, **not** a `pytest.mark.asyncio` marker — keep it
  that way so no `pytest-asyncio` dependency is required.
- `DataFeed.stream_bars` is an **async generator** (`async def … -> AsyncIterator[Bar]`,
  consumed with `async for`) — not a coroutine returning an iterator.

## Environment

- BUILD_SPEC targets Python 3.12; the dev machine has 3.10, so `pyproject.toml` sets
  `requires-python = ">=3.10"`. Code stays 3.10-safe (PEP 604 `X | None`, builtin
  generics OK). Bump back if standardizing on 3.12.
- Deps: `pandas`, `numpy`, `yfinance` (screening), `alpaca-py` (live 1-min feed),
  `python-dotenv` (`.env` loading). Install with `pip install -e ".[dev]"` in the venv.

## Status / roadmap

Build Order steps 1–6 are **built and tested**. The yfinance screener is
live-verified against Yahoo; the Alpaca live feed is code-complete and
unit-tested (bar mapping + credential guard) but its websocket streaming needs
real keys + market hours to exercise end-to-end. Next: step 7 (private strategy
module, AWS deploy, dashboard) — see `Trading_Bot_Context.md` for the full
architecture and the AWS/streaming/observability upskilling plan.
