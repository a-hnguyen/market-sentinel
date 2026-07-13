# Architecture — market-sentinel

`market-sentinel` is an asynchronous alert service, not an auto-trader. It
screens stocks, watches an approved set over Alpaca market data, and sends
Discord/console alerts when a setup arms or confirms. It never submits orders.

This document describes the code and AWS deployment as they exist now. The
README is the shorter entry point; this is the detailed current-state map.

> Private strategy values and inputs remain outside git in
> `alertengine/settings_local.py`, `alertengine/rules/_private/`, and
> `alertengine/data/`.

## Start here: the system in one picture

```text
                                    CONTROL
                         Discord commands or local REPL
                                      │
                                      ▼
                              WatchController
                        start / stop / resubscribe
                                      │
                                      ▼
  candidates.csv ──┐           ApprovalGate             manual watchlist
  live screen ─────┼────────── approved symbols ◀────── Discord /watch
  REPL approve ────┘                 │
                                      ▼
                           AlpacaFeed websocket
                              live 1-minute bars
                                      │
                                      ▼
                              BarAggregator
                         clock-aligned 2-minute bars
                                      │
                                      ▼
                               AlertEngine
               history → alert window → BB/RSI rules → state machines
                                      │
                        ┌─────────────┴─────────────┐
                        ▼                           ▼
                ConsoleNotifier              DiscordBot
                stdout + alerts.log          embeds + commands
```

The overnight pre-screen is a separate batch flow. It writes
`candidates.csv`; it does not run inside the live websocket loop.

```text
EventBridge (10:00 UTC weekdays)
             │
             ▼
Lambda holiday guard ──▶ SSM Run Command ──▶ systemd pre-screen unit
                                                   │
                         curated watchlist.xls ─────┤
                                                   ▼
                                      Alpaca historical REST
                                      RSI on 4h and 1h bars
                                                   │
                                                   ▼
                                           candidates.csv
                                                   │
                                  restart engine ──┘
```

## Runtime modes

All modes build the same `AlertEngine`; only the adapters and control surface
change.

| Command | Screener | Data feed | Control and alerts |
|---|---|---|---|
| `python -m alertengine` | mock | synthetic 1-min bars | local REPL + console |
| `python -m alertengine --replay` | yfinance | historical Alpaca REST replay | local REPL + console |
| `python -m alertengine --live` | yfinance | live Alpaca websocket | local REPL + console |
| `python -m alertengine --live --headless` | yfinance | live Alpaca websocket | Discord + console; production systemd mode |

`--prescreen` may be added to a live/replay startup to refresh the candidates
first. Production normally uses the separately scheduled pre-screen unit.

## Component ownership

The easiest way to understand the code is by asking which object owns each
kind of state or decision.

| Component | Owns | Does not own |
|---|---|---|
| `AlertEngine` | per-symbol bar history, buy/sell confirmation machines, rule evaluation | websocket retries, approved-symbol persistence |
| `AlertWindow` | `HH:MM` parsing, Pacific/DST conversion, normal and overnight window checks | market data filtering |
| `WatchController` | the long-running watch task, reconnect supervision, dynamic subscriptions, manual-symbol persistence | indicator/rule state |
| `ApprovalGate` | current in-memory approved-symbol set | provenance or durable storage |
| `BarAggregator` | partial clock-aligned 2-minute buckets per symbol | historical indicator state |
| `AlpacaFeed` | REST requests and one websocket connection attempt | retry scheduling after a failed socket |
| `DiscordBot` | command authorization, slash commands, alert embeds, background manual pre-screen job | trading logic |
| `PreScreener` | 4h/1h RSI confluence | live BB/RSI alert decisions |

This separation is deliberate. For example, a websocket failure escapes
`AlpacaFeed`; `WatchController` logs it and creates a fresh subscription after a
10-second delay. The engine never needs to know why the feed restarted.

## One completed-bar journey

1. Alpaca sends 1-minute bars over its websocket.
2. `BarAggregator` groups bars into even-minute buckets such as
   `09:30/09:31 → 09:30`. A missing minute may produce a valid one-bar bucket.
3. `AlertEngine` appends the completed 2-minute bar to the symbol's bounded
   history.
4. `AlertWindow` converts an aware timestamp to `America/Los_Angeles` and checks
   the inclusive `WINDOW_START`/`WINDOW_END` range.
   - Outside the window, history still stays warm, but neither rule runs.
   - Any armed/cooldown state resets, so one window cannot confirm in another.
   - Equal endpoints mean always open; a start after the end crosses midnight.
5. Inside the window, the buy and optional sell rules evaluate the same shared
   history.
6. A setup alert arms its direction-specific state machine. The arming bar does
   not count toward confirmation.
7. Two consecutive green closes confirm BUY; two consecutive red closes confirm
   SELL. A timeout resets an unconfirmed machine. A cooldown suppresses repeats.
8. `MultiNotifier` sends the alert to the console/log and Discord.

REST backfill runs before a live subscription and seeds history without
evaluating rules or sending alerts. It is a best-effort recent wall-clock
lookback and may be empty off-hours; the engine then warms naturally from live
bars.

## Watchlist lifecycle

Three sources feed the same in-memory `ApprovalGate`:

- scheduled/manual pre-screen survivors from `candidates.csv`;
- symbols added manually through Discord `/watch` or the REPL;
- results explicitly approved after `/screen` or REPL `screen`.

On production startup, `run_discord()` loads persisted manual symbols, then
loads `candidates.csv`, then starts the watcher if the union is non-empty.
`WatchController` restarts the websocket whenever the gate changes.

Only Discord `/watch` symbols are stored in
`alertengine/data/manual_watchlist.txt`. `ApprovalGate` itself does not track
where a symbol came from. Consequently `/unwatch` removes that symbol from the
current gate; a symbol still present in `candidates.csv` can return after the
next service restart.

`/stop confirm:true` stops market streaming only. The Discord bot and systemd
service remain online, the watchlist remains intact, and `/start` resumes it.

## Pre-screen lifecycle

All pre-screen entry points call `run_prescreen()`:

- `python -m alertengine.prescreen` — standalone/scheduled; checks the Alpaca
  market calendar unless `--force` is supplied;
- `python -m alertengine --live --prescreen` — refresh before startup;
- REPL `prescreen` — synchronous local refresh;
- Discord `/prescreen` — launches a child process, immediately acknowledges the
  interaction, and posts the result later.

The deployed schedule is EventBridge `cron(0 10 ? * MON-FRI *)`, or 10:00 UTC
(02:00 PST / 03:00 PDT). Lambda skips its configured holiday dates, then asks
SSM to start `market-sentinel-prescreen.service` by instance tag. The on-box
command performs a second calendar check, scans historical data in bounded
20-symbol batches, writes the CSV, and restarts the engine. Both the systemd job
and Discord background job are capped at five minutes.

## The swappable seams

`alertengine/interfaces.py` defines the four adapter boundaries:

```python
class Screener:
    async def get_candidates(self) -> list[Candidate]: ...

class DataFeed:
    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]: ...

class AlertRule:
    def evaluate(self, symbol: str, bars: list[Bar]) -> Alert | None: ...

class Notifier:
    async def send(self, alert: Alert) -> None: ...
```

`__main__.py` is the composition root: it chooses concrete implementations and
constructs the engine. `CandidateSink` is a fifth, batch-only seam inside
`prescreen/sinks.py`.

## Production deployment

The current deployment is intentionally a lean single box:

```text
GitHub push to main
        │
        ▼
GitHub Actions: Black + pytest
        │ OIDC assume-role
        ▼
SSM Run Command ──▶ redeploy.sh ──▶ git fetch/reset + pip install
                                      │
                                      ▼
                             restart config + engine units

EC2 (Amazon Linux 2023, t3.micro by default)
  ├─ market-sentinel-config.service
  │    ├─ SSM SecureString → /etc/market-sentinel/engine.env
  │    └─ private S3 overlay → git-ignored files
  ├─ market-sentinel.service
  │    └─ python -m alertengine --live --headless
  └─ market-sentinel-prescreen.service (oneshot, schedule is off-box)
```

Security and operations:

- the security group has no inbound rules; all service connections are
  outbound and administration uses SSM Session Manager/Run Command;
- EC2 uses an instance role and IMDSv2; GitHub Actions uses OIDC, so neither
  path stores AWS access keys;
- SSM Parameter Store holds runtime credentials/IDs; the private S3 bucket holds
  private strategy files and the curated watchlist;
- application logs currently live in systemd `journald` and are read through
  SSM; Terraform creates a CloudWatch engine log group, but no agent currently
  ships the journal into it;
- a CloudWatch EC2 status-check alarm and the systemd crash-loop `OnFailure`
  hook both publish infrastructure alerts through SNS;
- Lambda writes its own execution logs to its managed CloudWatch log group.

Local `candidates.csv`, `alerts.log`, and the manual watchlist survive process
restarts but not replacement of the EC2 root volume. S3 is currently an input
overlay, not an application-state backup.

## Failure behavior

| Failure | Current response |
|---|---|
| Alpaca websocket exits/errors | propagate to `WatchController`; retry with a fresh client after 10 seconds |
| Historical Alpaca request times out/connects poorly | bounded connect/read timeouts and one retry, in 20-symbol batches |
| Watchlist changes | cancel old watch task with a bound, clear partial aggregator buckets, resubscribe |
| Discord `/prescreen` runs long | child process killed after five minutes; bot/watcher stay responsive |
| Scheduled pre-screen runs long | systemd kills the oneshot after five minutes |
| Engine repeatedly crashes | systemd stops after its start limit and triggers SNS failure notification |
| EC2 becomes unhealthy/disappears | CloudWatch status-check alarm publishes to SNS |
| yfinance screen fails | return the process's last successful screen result |

## Where to make common changes

| Goal | Primary location |
|---|---|
| Change private thresholds/window | git-ignored `alertengine/settings_local.py` |
| Change public defaults | `alertengine/settings.py` |
| Add an alert strategy | implement `AlertRule`, wire it in `__main__.py` |
| Change command behavior | `discord_bot.py` and/or `repl.py` |
| Change subscription lifecycle | `watch_controller.py` |
| Change bar construction | `aggregator.py` and `tests/test_aggregator.py` |
| Change overnight scan | `prescreen/` |
| Change AWS resources | `infra/terraform/` |
| Change on-box startup/deploy | `infra/systemd/` and `infra/scripts/` |

## Deferred architecture

There is no RDS, web API, Kinesis/Kafka, Prometheus/Grafana, broker, or order
execution today. A future web/multi-user shape can add durable storage and a UI
behind the existing seams, but it should not be described as current behavior.
