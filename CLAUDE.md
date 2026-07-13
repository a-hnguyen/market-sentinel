# CLAUDE.md — market-sentinel

Async Python **alert engine** (not an auto-trader). It screens a stock universe
on demand, watches human-approved symbols on 2-min bars, and sends armed +
confirmed buy/sell alerts to a private Discord channel. Discord slash commands
are the deployed remote control; the local REPL remains for development. **No
orders are ever placed.** See `ARCHITECTURE.md` for the current runtime and AWS
design.

## Non-negotiable rules

- **IP boundary — never commit private strategy.** The repo is intended to go
  public, so only the public layer-1 rule (`alertengine/rules/bb_rsi_rule.py`)
  and mocks ship. The private strategy logic goes in git-ignored
  `alertengine/rules/_private/`; real tuned params/criteria go in git-ignored
  `alertengine/settings_local.py` (which overrides `settings.py`). Hidden logic
  must be **ABSENT, not obfuscated**. `.gitignore` must keep covering `.env`,
  `rules/_private/`, `settings_local.py`, secrets, logs, private notes, and
  reference PDFs before any commit. Treat `.gitignore` as the authoritative list.
- **yfinance is screening ONLY, never the trade/data path.** Bars come from the
  DataFeed (mock/replay locally, Alpaca live). Keep the two sources separate.
- **The four seams are load-bearing** — `Screener`, `DataFeed`, `AlertRule`,
  `Notifier` (`alertengine/interfaces.py`) plus the `ApprovalGate`. Don't merge
  or rename them to "simplify"; swapping mock→real (Alpaca/yfinance) and adding a
  dashboard/IBKR later depends on these boundaries staying intact.
- **Build incrementally.** Don't one-shot the app.
  The aggregator is the one place a silent bug poisons everything downstream —
  its test must pass before building on top.

## Architecture

Read `ARCHITECTURE.md` first for the full current-state walkthrough. The compact
path is:

```
Discord/REPL ─▶ WatchController ─▶ [ApprovalGate] ─▶ DataFeed(1-min)
                                                          │
                                                   aggregator(2-min)
                                                        │
                                              history + AlertWindow
                                                        │
                                              indicators (BB, RSI)
                                                        │
                                         buy/sell AlertRules
                                                        │
                              confirmation machines/cooldown ─▶ Notifier
```

- `alertengine/aggregator.py` — folds 1-min → clock-aligned 2-min bars
  (flush-on-advance; handles IEX missing-minute). Alpaca has no native 2-min
  stream, hence the aggregation.
- `alertengine/engine.py` — owns per-symbol 2-min history and confirmation
  machines. Keeps `AlertRule` stateless.
- `alertengine/alert_window.py` — owns strict time parsing and Pacific/DST window
  checks. Outside-window bars warm history but cannot evaluate or advance alerts.
- `alertengine/watch_controller.py` — owns start/stop/restart of the active
  subscription when Discord or the REPL changes the watchlist.
- `alertengine/discord_bot.py` — allowlisted slash commands + alert delivery;
  connects outbound, with no inbound EC2 ports.
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
python -m alertengine --replay     # historical Alpaca bars through the same engine
python -m alertengine --live       # live — yfinance screen + Alpaca 1-min feed
python -m alertengine --live --headless  # production Discord control
# REPL: screen → approve <SYMS> → watch → status → quit
```

Live mode needs `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` in a `.env` (copy
`.env.example`). Headless mode also needs the four `DISCORD_*` values.
Mock<->live only swaps which Screener/DataFeed `__main__` builds; the engine is
identical.

- Tests use `asyncio.run(...)`, **not** a `pytest.mark.asyncio` marker — keep it
  that way so no `pytest-asyncio` dependency is required.
- `DataFeed.stream_bars` is an **async generator** (`async def … -> AsyncIterator[Bar]`,
  consumed with `async for`) — not a coroutine returning an iterator.

## Environment

- `pyproject.toml` sets `requires-python = ">=3.10"`; local development uses
  3.10, CI/deploy use 3.11, and the Windows guide recommends 3.12. Code stays
  3.10-safe (PEP 604 `X | None`, builtin generics OK).
- Deps: `pandas`, `numpy`, `yfinance` (screening), `alpaca-py` (live 1-min feed),
  `discord.py` (remote control/alerts), `python-dotenv` (`.env` loading). Install
  with `pip install -e ".[dev]"` in the venv.

## Documentation convention

- `README.md` is the public entry point; `ARCHITECTURE.md` describes current
  behavior and ownership; `infra/README.md` is the production runbook.
- Git-ignored private notes are non-authoritative and must not be referenced by
  public-facing documentation.
- When runtime behavior changes, update the current-state docs in the same pass.
  Verify commands, schedules, persistence, logging, and failure behavior against
  code/IaC rather than copying old plans forward.

## Status / roadmap

Build Order steps 1–10 are **built and tested**. The lean AWS stack, scheduled
pre-screen, CI/CD, Discord control/alerts, and live Alpaca service are deployed.
Application logs currently live in journald and are inspected through SSM; do
not claim the provisioned CloudWatch engine log group is receiving them until a
shipping agent is actually configured. See `ARCHITECTURE.md` for current status.
