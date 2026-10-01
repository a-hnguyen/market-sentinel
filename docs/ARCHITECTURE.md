# Architecture — market-sentinel

An asynchronous stock-alert service for a single user. It watches selected
symbols, detects a setup, waits for confirmation, and sends Discord alerts.
The user decides whether to trade. **The application never places orders.**

This describes the current code and Terraform configuration, not a claim that
every optional feature is enabled or that a deployment is healthy. For commands
and logs, use [the operations runbook](../infra/README.md).

## The one-minute explanation

> I built a Python service that monitors market data and sends actionable
> alerts through Discord, which also acts as its remote control. I separated
> the data providers, rules, and notifications behind interfaces so I could
> test the same engine with mock, historical, and live data. The engine owns
> per-stock history and a small state machine that separates a possible setup
> from a confirmed alert. It runs on EC2 because it maintains long-lived
> connections. Terraform provisions the AWS resources, GitHub Actions deploys
> through short-lived OIDC credentials, and CloudWatch collects structured
> logs. I kept it single-instance rather than adding a database or message
> broker before there was a real need for one.

The strongest interview themes are state ownership, provider isolation,
bounded retries, deployment/security, and deliberate right-sizing—not a claim
of high-scale trading or guaranteed investment results.

## Two workloads, one box

The continuous watcher and the scheduled scan solve different problems:

- **Watcher:** given a watchlist, decide when an alert is warranted.
- **Pre-screen:** scan a curated spreadsheet and choose candidates to watch.

```text
Discord commands / local REPL
              │
              ▼
       WatchController ──▶ ApprovalGate (current approved symbols)
              │
              ▼
       Alpaca websocket (completed 1-minute bars)
              │
              ▼
       BarAggregator (native 1-minute or configured multi-minute candles)
              │
              ▼
       AlertEngine
       bounded history → alert window → rules → confirmation machines
              │
              ▼
       MultiNotifier ──┬─▶ console / alert log
                       └─▶ Discord lifecycle cards + confirmation messages
```

The pre-screen runs in a separate process on the same instance:

```text
EventBridge Scheduler (3 PM Pacific, weekdays)
    → Lambda holiday guard
    → SSM Run Command
    → systemd pre-screen service on EC2
    → watchlist.xlsx + Alpaca historical REST
    → 4-hour / 1-hour RSI agreement in either direction
    → candidates.csv + audit report + Discord summary
    → restart engine → load the new automatic watchlist
```

**Lambda triggers the work; it does not run the scan.** This avoids packaging
the data-analysis dependencies and private files into another runtime. The
systemd pre-screen `.service` is still needed; only the old `.timer` was removed.

The interactive Yahoo Finance screen is separate from this spreadsheet-based
batch job. Its output enters the watchlist only when explicitly approved.

An optional second watcher polls Robinhood historical bars through an
allowlisted, read-only MCP adapter. It has its own watchlist, controller, and
engine state; it shares the implementation and Discord delivery, not the
regular watcher's state. It is disabled by default and exposes no order tool.

## File map: who owns what?

Paths below are relative to `alertengine/` unless an `infra/` prefix is shown.
Package `__init__.py` files expose packages; the runtime responsibilities are:

| Files | Responsibility |
|---|---|
| `__main__.py` | Composition root: choose adapters, register strategies, start REPL or Discord |
| `interfaces.py`, `models.py` | Adapter contracts and shared `Bar`, `Candidate`, `Alert` types |
| `strategy.py`, `settings.py` | Strategy configuration and publishable defaults; private overrides are injected separately |
| `engine.py` | Per-symbol history, rule evaluation, independent BUY/SELL state machines, notification de-duplication |
| `aggregator.py`, `indicators.py` | Candle construction and indicator calculations |
| `alert_window.py`, `market_session.py` | Timezone-aware alert eligibility and market-session labels |
| `gate.py`, `watch_controller.py` | Approved set; subscription start/stop/retry; manual/automatic watchlist ownership and persistence |
| `repl.py`, `discord_bot.py` | Local/remote commands; Discord authorization, lifecycle cards, background scans |
| `feeds/alpaca_feed.py`, `feeds/alpaca_replay_feed.py`, `feeds/mock_feed.py` | Live stream and historical requests, replay, synthetic test feed |
| `screeners/yfinance_screener.py`, `screeners/mock_screener.py` | Interactive candidate screening, real or synthetic |
| `rules/bb_rsi_rule.py`, `rules/bb_rsi_exit_rule.py` | Public example setup rules; actual private rules remain outside git |
| `notifiers/console_notifier.py`, `notifiers/multi_notifier.py`, `notifiers/tagged_notifier.py` | Console/log delivery, fan-out, optional watcher labels; Discord bot also implements `Notifier` |
| `prescreen/__main__.py`, `prescreen/runner.py`, `prescreen/calendar.py` | Batch entry point, shared pipeline, trading-day guard |
| `prescreen/watchlist.py`, `prescreen/screener.py`, `prescreen/sinks.py`, `prescreen/reporting.py` | Spreadsheet input, timeframe agreement, CSV output, audit report/Discord summary |
| `feeds/robinhood_feed.py`, `feeds/historical_polling_feed.py` | Normalize historical responses; poll completed minutes with overlap and timestamp de-duplication |
| `feeds/robinhood_mcp_transport.py`, `robinhood_auth.py` | Allowlisted read-only transport, OAuth storage/refresh and local authorization bootstrap |
| `logging_config.py` | JSON logging to stderr and optional rotating production files |
| `infra/terraform/` | AWS resources, IAM policies, remote state, scheduler and CI trust |
| `infra/systemd/`, `infra/scripts/`, `infra/lambda/prescreen_trigger/handler.py` | Process lifecycle, bootstrap/redeploy/config/log shipping, thin scheduled trigger |
| `tests/` | Public regression tests with fake providers; private rules have separate ignored tests |

## How confirmation works

Each watched symbol has a BUY machine and, when configured, a separate SELL
machine. They share market history but keep their own timers and confirmation
state.

```text
WAITING ── setup found ──▶ ARMED ── confirmation passes ──▶ COOLDOWN
   ▲                        │                                  │
   └──── window expires ────┘                                  │
   └──── setup clears + minimum cooldown elapses ───────────────┘
```

At arming, the engine records the candle's timestamp and a snapshot of its
setup values. An optional `ArmedTriggerRule` receives that fixed reference and
the current history. It can combine observations across candles without owning
mutable per-symbol state. The public example instead confirms with a candle
pattern; `ConfirmationRule` can add a post-pattern gate.

BUY and SELL may use different timeout lengths. Timeouts count subsequent
completed strategy bars; they are not independent wall-clock alarms. Success
on the last permitted bar wins over expiration. Private values and confirmation
logic stay in the ignored overlay, not in this document.

Outside the configured Pacific alert window, bars still warm indicator history,
but rules do not run and pending machine state resets. A timeout also resets
the setup reference; it cannot be carried into the next attempt. Depending on
strategy configuration, history may be retained. Shared history is never
cleared if that would blind an active peer direction.

Discord creates an armed card, then edits it to `CONFIRMED` or `EXPIRED`.
Confirmation also posts a fresh message so phone notifications do not rely on
message edits. The expiration display is a Discord timestamp, not a continuously
updated bot message. Yahoo/Robinhood links open research or stock pages; they
cannot place or prefill orders.

The regular watcher can limit each symbol/alert kind to one delivery per
Pacific day, while configured strategies allow repeated setup/expiration
lifecycles. **That delivery history is in memory, not durable exactly-once
delivery.** A process restart resets it and loses old card handles.

## Why these design choices?

**Interfaces, not a separate engine per provider.** `Screener`, `DataFeed`,
`AlertRule`, and `Notifier` are the original boundaries. `HistoricalBarFeed`
adapts range-query providers, and `CandidateSink` isolates batch output.
Mock/replay/live reuse the engine. A new provider still needs normalization and
tests, but it does not need a copy of the trading state machine.

**One owner for retries.** `AlpacaFeed` performs one websocket attempt;
`WatchController` supervises it and retries after a delay. Historical requests
have bounded timeouts and batches. This avoids a dependency retry loop starving
the event loop that also serves Discord commands.

**One owner for state.** Rules calculate signals; the engine owns history and
confirmation state; the controller owns subscriptions and watchlist provenance.
Adding a symbol therefore resubscribes the feed instead of just changing a set
that an already-open websocket never sees.

**Separate heavy batch work from responsive controls.** Discord `/prescreen`
starts a child process and applies its results to the running controller.
The scheduled service runs separately and restarts the engine after success.
Both paths have a five-minute execution bound.

**Recompute market data; persist user choices.** REST backfill warms indicators
silently at subscription startup. It looks back seven calendar days, keeps a
bounded tail, and merges overlapping timestamps without duplicate history.
Manual watchlists and the selected strategy are worth persisting; old setup
timers are deliberately not restored.

**Single-instance AWS, not microservices for their own sake.** The service holds
long-lived market-data and Discord connections. EC2 is a straightforward fit.
Lambda handles the small scheduled edge. There is no current requirement for
a managed database, broker, Kubernetes, or independently scaled services.

## AWS: each service has a job

| Service/tool | Job in this project |
|---|---|
| EC2 + EBS | Always-on compute and its local disk |
| systemd (Linux, not AWS) | Keep the engine running; execute config/pre-screen units; signal crash loops |
| IAM | Define what the instance, scheduler, Lambda, and CI roles may access |
| SSM Parameter Store | Encrypted runtime credentials and Discord configuration |
| SSM Session Manager / Run Command | Shell access and automation without inbound SSH |
| S3 | Private configuration/rules, persisted user choices/OAuth, separate Terraform state bucket |
| EventBridge Scheduler + Lambda | Timezone-aware weekday schedule and lightweight holiday-gated trigger |
| CloudWatch + SNS | Structured logs, EC2 status alarm, infrastructure-health notifications |
| Terraform | Declare/provision infrastructure and retain its state in S3 with native locking |
| GitHub Actions + OIDC | Test pushes/PRs; obtain temporary AWS credentials to redeploy `main` |
| Resource Groups | Tag-based console view, not an application dependency |

EC2 has no inbound security-group rules and requires IMDSv2 for metadata access.
Its instance role supplies AWS credentials; CI assumes a scoped role through
OIDC. Third-party tokens remain
in SSM or the private S3 overlay. Discord commands check guild, channel, and
user allowlists. This is a single-user control plane, not an app with its own
accounts or tenant isolation.

## What survives a restart?

| State | Stored where | Process restart | EC2/root-volume replacement |
|---|---|---|---|
| Indicator history | Memory; REST backfill | Rebuilt | Rebuilt |
| Timers, cooldown, daily delivery gate, Discord card handles | Memory | Lost | Lost |
| Automatic candidates + audit report | Local CSV/JSON | Survive | Re-run pre-screen |
| Manual watchlists + selected strategy | Local files, mirrored to private S3 | Survive | Restored from S3 if upload succeeded |
| Optional Robinhood OAuth state | Protected local file, mirrored to S3 | Survives | Restored from S3 if upload succeeded |
| Logs | journald, local files, retained CloudWatch copies | Local/remote logs remain subject to retention | Only shipped CloudWatch copies remain |

S3 persistence is best-effort: local changes still apply if upload fails, and
Discord reports a warning. The instance is a single point of failure; neither
S3 backups nor a running process make this a highly available system.

## Deployment and failure boundaries

```text
Push main → GitHub Actions tests → OIDC → SSM → redeploy.sh
                                              │
                       pull code + install package/units
                       refresh SSM config + private S3 overlay
                       restart engine
```

Terraform changes AWS resources. GitHub changes application code and unit files.
Private overlay changes reach EC2 when the config service is refreshed. These
are separate delivery paths; [the runbook](../infra/README.md) explains each.

The CloudWatch agent reads rotating JSON files in `/var/log/market-sentinel/`,
not the journal directly. The same structured output also goes to stderr, which
systemd captures in journald. Application logs have 14-day CloudWatch retention;
Lambda has its own log group.

| Failure | Current response / limitation |
|---|---|
| Market stream fails | Controller retries with a fresh client after 10 seconds |
| Pre-screen fails or times out | Job reports failure; no successful-result handoff; bot stays responsive on the manual path |
| Engine crash-loops | systemd restart limit, then `OnFailure` publishes to SNS |
| EC2 fails status checks | CloudWatch publishes to the same SNS ops topic |
| SSM agent or Discord is unreachable | Remote control/deploy can fail even if the process/EC2 status looks healthy; no separate agent heartbeat alarm |
| yfinance screen fails | Last successful in-process screen is returned |

The Lambda trigger reports command submission, not scan completion. Inspect
the pre-screen logs/Discord report for the result. Its holiday list needs yearly
maintenance; the on-box calendar is a second check and fails open on lookup errors.

## What is not built

No RDS/shared database, web UI/API, distributed message broker, Prometheus/Grafana,
broker execution, or automated orders. A web/multi-user version would need a
durable control/state model and authorization—not just a different notifier.
These are possible next steps, not services to claim as deployed today.
