# market-sentinel

An asynchronous stock-alert service that watches approved symbols over Alpaca
market data and sends setup/confirmation alerts to a private Discord channel.
It is an alerting tool, not an auto-trader: it never submits orders.

An optional second watcher polls Robinhood's authenticated MCP endpoint for
24/5 one-minute bars and applies the
same alert pipeline to a separately managed overnight watchlist. Its adapter
exposes historical reads only; no order tool is available to the application.

## How it works

```text
Discord or local REPL
        │
        ├─ regular watchlist ─▶ Alpaca websocket ───────┐
        │                                               │
        └─ overnight watchlist ─▶ Robinhood MCP poll ───┤
                                                        ▼
                                      configured bar aggregation
                                                        │
                                   alert window + BB/RSI rules
                                                        │
                                      buy/sell confirmation state
                                                        │
                                           console + Discord alerts
```

A separate post-close pre-screen evaluates a curated watchlist over
regular-session-only 4-hour and 1-hour RSI data. It selects stocks that are
oversold on both timeframes or overbought on both and writes their labeled union
to `candidates.csv`. Production runs on one EC2 instance under systemd; EventBridge
Scheduler, Lambda, and SSM trigger it without opening inbound ports.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) next for the component-by-component
walkthrough, runtime sequences, persistence boundaries, and failure behavior.

## Run locally

Python 3.10 or newer is supported.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

python -m alertengine              # mock data, local REPL
python -m alertengine --replay     # historical Alpaca data, local REPL
python -m alertengine --live       # live Alpaca data, local REPL
```

Live/replay modes require `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`; copy
`.env.example` to the git-ignored `.env`. Private strategy values belong in the
git-ignored `alertengine/settings_local.py` override.

In the REPL, a minimal flow is:

```text
screen
approve AAPL
watch
status
stop
quit
```

Replay still enforces the configured alert window against historical bar times.

## Verify changes

```bash
.venv/bin/black --check alertengine tests
.venv/bin/pytest tests/ -q
```

## Documentation map

| Document | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Best starting point for current runtime and AWS architecture |
| [docs/DISCORD_SETUP.md](docs/DISCORD_SETUP.md) | Bot creation, authorization, commands, and SSM configuration |
| [docs/ROBINHOOD_WATCHER.md](docs/ROBINHOOD_WATCHER.md) | Optional read-only overnight watcher, OAuth, and operations |
| [docs/SETUP_WINDOWS.md](docs/SETUP_WINDOWS.md) | Optional Windows local-development walkthrough |
| [infra/README.md](infra/README.md) | Terraform deployment, schedule, operations, and logs |
| `CLAUDE.md` | Coding-agent constraints and repository conventions |

## Production boundaries

- Discord commands are restricted by guild, channel, and user-ID allowlists.
- EC2 has no inbound security-group rules; administration uses SSM.
- Runtime credentials are SSM SecureStrings; private strategy files arrive from
  a private S3 overlay.
- Structured engine and pre-screen logs are retained for 14 days in CloudWatch
  Logs and remain locally available through `journalctl`.
- The manual `/watch` list is backed up to the private S3 overlay and restored
  after EC2 replacement. Candidate CSVs and logs remain single-box state.
- The optional Robinhood watcher keeps its OAuth state and `/penny-watch` list
  in separate git-ignored files backed up to the same private S3 overlay.
- RDS, a web UI, Kinesis/Kafka, Prometheus/Grafana, brokers, and order execution
  are not part of the current system.
