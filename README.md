# market-sentinel

An asynchronous stock-alert service that watches approved symbols over Alpaca
market data and sends setup/confirmation alerts to a private Discord channel.
It is an alerting tool, not an auto-trader: it never submits orders.

## How it works

```text
Discord or local REPL
        │
        ▼
approved watchlist ─▶ Alpaca 1-min bars ─▶ 2-min aggregation
                                                │
                                                ▼
                              alert window + BB/RSI rules
                                                │
                                                ▼
                                  buy/sell confirmation state
                                                │
                                                ▼
                                      console + Discord alerts
```

A separate scheduled pre-screen evaluates a curated watchlist over historical
4-hour and 1-hour data and writes the survivors to `candidates.csv`. Production
runs on one EC2 instance under systemd; EventBridge, Lambda, and SSM trigger the
pre-screen without opening inbound ports.

Read [ARCHITECTURE.md](ARCHITECTURE.md) next for the component-by-component
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
| [ARCHITECTURE.md](ARCHITECTURE.md) | Best starting point for current runtime and AWS architecture |
| [DISCORD_SETUP.md](DISCORD_SETUP.md) | Bot creation, authorization, commands, and SSM configuration |
| [SETUP_WINDOWS.md](SETUP_WINDOWS.md) | Optional Windows local-development walkthrough |
| [infra/README.md](infra/README.md) | Terraform deployment, schedule, operations, and logs |
| `CLAUDE.md` | Coding-agent constraints and repository conventions |

## Production boundaries

- Discord commands are restricted by guild, channel, and user-ID allowlists.
- EC2 has no inbound security-group rules; administration uses SSM.
- Runtime credentials are SSM SecureStrings; private strategy files arrive from
  a private S3 overlay.
- Engine logs currently live in journald and are inspected through SSM.
- Local CSV/log/watchlist files are single-box state and do not survive EC2
  volume replacement.
- RDS, a web UI, Kinesis/Kafka, Prometheus/Grafana, brokers, and order execution
  are not part of the current system.
