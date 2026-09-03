# Architecture — market-sentinel

`market-sentinel` is an asynchronous alert service, not an auto-trader. It
screens stocks, watches an approved set over Alpaca market data, and sends
Discord/console alerts when a setup arms or confirms. It never submits orders.
An optional second engine watches a separately curated penny/swing list over
Robinhood 24/5 historical bars; its application adapter is read-only too.

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

The post-close pre-screen is a separate batch flow. It writes
`candidates.csv`; it does not run inside the live websocket loop.

```text
EventBridge Scheduler (3:00 PM America/Los_Angeles, weekdays)
             │
             ▼
Lambda holiday guard ──▶ SSM Run Command ──▶ systemd pre-screen unit
                                                   │
                         curated watchlist.xls ─────┤
                                                   ▼
                                      Alpaca historical REST
                                       regular session only
                                                   │
                              ┌────────────────────┴────────────────────┐
                              ▼                                         ▼
                    OVERSOLD: RSI < 30                        OVERBOUGHT: RSI > 70
                       on 4h and 1h                              on 4h and 1h
                              └────────────────────┬────────────────────┘
                                                   ▼
                                       union, labeled by signal
                                                   │
                              ┌────────────────────┴────────────────────┐
                              ▼                                         ▼
                       candidates.csv                         Discord audit summary
                   replace automatic set             both directions + deltas
                              │
                     restart engine
```

Yahoo Most Actives belongs to the separate interactive `screen` path. It does
not populate the curated spreadsheet or feed this scheduled RSI pre-screen.

The optional overnight path runs alongside, rather than inside, the Alpaca
stream:

```text
Discord /penny-watch ─▶ separate ApprovalGate + WatchController
                                      │
                                      ▼
                         Robinhood MCP historical read
                              1-minute 24/5 bars
                                      │
                     overlap polling + timestamp de-duplication
                                      │
                                      ▼
                   configured candles (currently native 1-minute)
                                      │
                                      ▼
                     independent AlertEngine state machines
                                      │
                                      ▼
                    tagged console + Discord alerts (no orders)
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
| `AlertEngine` | active strategy, per-symbol bar history, buy/sell confirmation machines, rule evaluation, optional post-pattern confirmation rule | websocket retries, approved-symbol persistence |
| `AlertWindow` | `HH:MM` parsing, Pacific/DST conversion, normal and overnight window checks | market data filtering |
| `WatchController` | watch task, reconnect supervision, dynamic subscriptions, automatic/manual provenance, manual persistence | indicator/rule state |
| `ApprovalGate` | current in-memory union of approved symbols | durable storage |
| `BarAggregator` | native 1-minute pass-through or partial clock-aligned multi-minute buckets per symbol | historical indicator state |
| `AlpacaFeed` | REST requests and one websocket connection attempt | retry scheduling after a failed socket |
| `DiscordBot` | command authorization, slash commands, alert embeds, background manual pre-screen job | trading logic |
| `PreScreener` | 4h/1h RSI confluence | live BB/RSI alert decisions |
| `RobinhoodHistoricalFeed` | request batching and response normalization | OAuth, polling, strategy logic |
| `HistoricalPollingFeed` | completed-minute polling, overlap recovery, de-duplication | provider authentication, indicator rules |

This separation is deliberate. For example, a websocket failure escapes
`AlpacaFeed`; `WatchController` logs it and creates a fresh subscription after a
10-second delay. The engine never needs to know why the feed restarted.

## One completed-bar journey

1. Alpaca sends completed 1-minute bars over its websocket.
2. `BarAggregator` passes native 1-minute strategy bars through immediately or
   groups them into clock-aligned multi-minute buckets. A missing minute may
   produce a valid partial bucket.
3. `AlertEngine` appends the completed strategy bar to the symbol's bounded
   history.
4. `AlertWindow` converts an aware timestamp to `America/Los_Angeles` and checks
   the inclusive `WINDOW_START`/`WINDOW_END` range.
   - Outside the window, history still stays warm, but neither rule runs.
   - Any armed/cooldown state resets, so one window cannot confirm in another.
   - Equal endpoints mean always open; a start after the end crosses midnight.
5. Inside the window, the buy and optional sell rules evaluate the same shared
   history.
   - Alerts based on bars before the 06:30 Pacific regular-session open are
     labelled `PREMARKET` in Discord and console output.
6. A setup alert arms its direction-specific state machine. The arming bar does
   not count toward confirmation.
7. Two consecutive green closes confirm the public BUY pattern; two consecutive
   red closes confirm SELL. An optional `ConfirmationRule` may apply additional
   private checks after the BUY pattern, while an `ArmedTriggerRule` can replace
   the candle pattern for either direction. A timeout still bounds the armed
   state, and a cooldown suppresses repeats.
8. For the regular watcher, a delivery gate permits each symbol/alert kind only
   once per Pacific calendar day. The state machines continue processing any
   suppressed repeats. The penny watcher retains repeat-after-cooldown behavior.
9. `MultiNotifier` sends permitted alerts to the console/log and Discord.

REST backfill runs before a live subscription and seeds history without
evaluating rules or sending alerts. It is a best-effort recent wall-clock
lookback and may be empty off-hours; the engine then warms naturally from live
bars.

For Robinhood, gap-filled bars carry `interpolated=true`. They retain clock
alignment for indicators, but a fully synthetic two-minute candle cannot arm a
setup or count as a green/red confirmation. Mixed two-minute buckets derive
OHLCV from real traded minutes only.

## Watchlist lifecycle

Three sources feed the same in-memory `ApprovalGate`:

- scheduled/manual pre-screen survivors from `candidates.csv`;
- symbols added manually through Discord `/watch` or the REPL;
- results explicitly approved after `/screen` or REPL `screen`.

On production startup, `run_discord()` loads persisted manual symbols, then
loads `candidates.csv` as the automatic set, then starts the watcher if the
union is non-empty.
`WatchController` restarts the websocket whenever the gate changes.

`WatchController._automatic` tracks the latest pre-screen set in memory and
`candidates.csv` persists it across restarts. `WatchController._manual` tracks
explicit `/watch` choices and `alertengine/data/manual_watchlist.txt` persists
them locally. In production, each manual change also uploads the full list to
`private/runtime/manual_watchlist.txt` in the private S3 overlay; the config
service restores it before the bot starts on a new instance. `ApprovalGate`
contains their active union. A new pre-screen replaces the
automatic set: disappeared candidates are ejected, while overlapping or manual
symbols remain. `/unwatch` removes a symbol from the current gate; if it passes
a future pre-screen it can be automatically selected again.

The active strategy is selected from the registry supplied at startup.
`/strategy` changes it through `WatchController`, which resets incompatible
history and armed state, restarts an active feed, and atomically persists the
name in `alertengine/data/active_strategy.txt`. Production mirrors that file to
the private S3 overlay and restores it before the service starts.

`/stop confirm:true` stops market streaming only. The Discord bot and systemd
service remain online, the watchlist remains intact, and `/start` resumes it.

When enabled, `/penny-watch`, `/penny-unwatch`, `/penny-watchlist`,
`/penny-start`, `/penny-stop`, and `/penny-status` operate only on the second
watcher. Its local `penny_watchlist.txt` and S3 object are independent of the
regular manual and automatic sets.

## Pre-screen lifecycle

All pre-screen entry points call `run_prescreen()`:

- `python -m alertengine.prescreen` — standalone/scheduled; checks the Alpaca
  market calendar unless `--force` is supplied;
- `python -m alertengine --live --prescreen` — refresh before startup;
- REPL `prescreen` — synchronous local refresh;
- Discord `/prescreen` — launches a child process, immediately acknowledges the
  interaction, and posts the result later.

The deployed EventBridge Scheduler expression is `cron(0 15 ? * MON-FRI *)`
with timezone `America/Los_Angeles`, so it stays at 3:00 PM through daylight
saving changes. Lambda skips configured market holidays, then asks SSM to start
`market-sentinel-prescreen.service` by instance tag. The on-box command performs
a second calendar check, fetches 30-minute historical bars in bounded batches,
keeps only 09:30–16:00 ET regular-session bars, and aggregates them into
market-open-aligned 4-hour and 1-hour closes. It selects each direction only
when both timeframes agree, writes the labeled overbought/oversold union, reports
both directions plus additions/removals to Discord, and restarts the engine.
Both the systemd job and Discord background job are capped at five minutes.

## The swappable seams

`alertengine/interfaces.py` retains the four original adapter boundaries and
adds range-based historical-data and strategy-confirmation extensions:

```python
class Screener:
    async def get_candidates(self) -> list[Candidate]: ...

class DataFeed:
    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]: ...

class HistoricalBarFeed:
    async def fetch_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> list[Bar]: ...

class AlertRule:
    def evaluate(self, symbol: str, bars: list[Bar]) -> Alert | None: ...

class ConfirmationRule:
    def evaluate(self, symbol: str, bars: list[Bar]) -> dict[str, float] | None: ...

class ArmedTriggerRule:
    def evaluate(self, symbol: str, bars: list[Bar]) -> dict[str, float] | None: ...

class Notifier:
    async def send(self, alert: Alert) -> None: ...
```

`StrategyConfig` groups these rule seams with the bar interval and arm timeout.
The public strategy is always registered; private settings may add selectable
strategies without putting their logic in tracked code. `__main__.py` is the
composition root that builds this registry and constructs the engine.
`CandidateSink` is a separate, batch-only seam inside `prescreen/sinks.py`.

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
  private strategy files, the active-strategy selection, curated watchlists,
  and refreshable Robinhood OAuth state; the token file is mode `0600` and never
  enters git;
- structured JSON application logs remain available in systemd `journald` and
  are also shipped by the CloudWatch agent into per-instance `engine` and
  `prescreen` streams with 14-day retention;
- a CloudWatch EC2 status-check alarm and the systemd crash-loop `OnFailure`
  hook both publish infrastructure alerts through SNS;
- Lambda writes its own execution logs to its managed CloudWatch log group.

Local `candidates.csv` and `alerts.log` survive process restarts but not EC2 root
volume replacement. The manual watchlist survives replacement through its S3
copy; automatic candidates can be rebuilt by the post-close pre-screen. The
penny watchlist and refreshable Robinhood OAuth state also survive through
their separate private S3 objects.

## Failure behavior

| Failure | Current response |
|---|---|
| Alpaca websocket exits/errors | propagate to `WatchController`; retry with a fresh client after 10 seconds |
| Historical Alpaca request times out/connects poorly | bounded connect/read timeouts and one retry, in 20-symbol batches |
| Watchlist changes | cancel old watch task with a bound, clear partial aggregator buckets, resubscribe |
| Strategy changes | validate the registered name, clear incompatible strategy state, persist the choice, and restart an active subscription |
| Discord `/prescreen` runs long | child process killed after five minutes; bot/watcher stay responsive |
| Scheduled pre-screen runs long | systemd kills the oneshot after five minutes |
| Engine repeatedly crashes | systemd stops after its start limit and triggers SNS failure notification |
| EC2 becomes unhealthy/disappears | CloudWatch status-check alarm publishes to SNS |
| yfinance screen fails | return the process's last successful screen result |
| Robinhood query/OAuth refresh fails | fail the watcher attempt; its controller retries after 10 seconds and logs the failure |

## Where to make common changes

| Goal | Primary location |
|---|---|
| Change private thresholds/window | git-ignored `alertengine/settings_local.py` |
| Change public defaults | `alertengine/settings.py` |
| Add an alert strategy | implement the rule seams and register a `StrategyConfig` in private settings |
| Change command behavior | `discord_bot.py` and/or `repl.py` |
| Change subscription lifecycle | `watch_controller.py` |
| Change bar construction | `aggregator.py` and `tests/test_aggregator.py` |
| Change post-close scan | `prescreen/` |
| Change AWS resources | `infra/terraform/` |
| Change on-box startup/deploy | `infra/systemd/` and `infra/scripts/` |

## Deferred architecture

There is no RDS, web API, Kinesis/Kafka, Prometheus/Grafana, broker, or order
execution today. A future web/multi-user shape can add durable storage and a UI
behind the existing seams, but it should not be described as current behavior.
