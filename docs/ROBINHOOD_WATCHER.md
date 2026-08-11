# Robinhood overnight watcher

This optional, alert-only watcher reads Robinhood 24/5 one-minute historical
bars through MCP at a configurable candle interval and runs an independent
copy of the existing buy/sell alert state machine. The application exposes only
`get_equity_historicals`; it has no generic MCP call or order method.

## Private settings

Add these values to the git-ignored `alertengine/settings_local.py`:

```python
PENNY_WATCHER_ENABLED = True
PENNY_WINDOW_START = "HH:MM"
PENNY_WINDOW_END = "HH:MM"
PENNY_BAR_INTERVAL_MINUTES = 1
PENNY_ARM_TIMEOUT_BARS = 30
```

A start after the end represents a Pacific-time window crossing midnight. The
one-minute configuration evaluates the same indicator periods on one-minute
candles; 30 timeout bars preserve the prior 30-minute setup-expiration window.
The public defaults leave the watcher disabled; real hours remain private.

## Bootstrap OAuth

From a trusted local checkout with dependencies installed:

```bash
.venv/bin/python -m alertengine.robinhood_auth
```

Open the printed Robinhood URL. The browser may end on a localhost connection
error because the CLI deliberately does not expose a callback server. Copy the
complete URL from the address bar and paste it into the prompt. The command
verifies that `get_equity_historicals` is available and writes mode-`0600`
OAuth state to the git-ignored `alertengine/data/robinhood_oauth.json`.

Upload that file and the updated private settings overlay:

```bash
BUCKET="$(terraform -chdir=infra/terraform output -raw overlay_bucket)"
aws s3 cp alertengine/settings_local.py \
  "s3://$BUCKET/private/settings_local.py"
aws s3 cp alertengine/data/robinhood_oauth.json \
  "s3://$BUCKET/private/runtime/robinhood_oauth.json"
```

The instance restores both at boot. OAuth refresh updates are written locally
and mirrored back to the same private S3 object.

## Operate and verify

After deploying, use the private Discord channel:

```text
/penny-watch AMC CELZ
/penny-status AMC
/penny-watchlist
/penny-stop confirm:true
/penny-start
```

CloudWatch logs include `historical_backfill` and `historical_poll` events with
returned/emitted real versus interpolated bar counts. Alerts include the
`penny-overnight` watcher label. Validate several live sessions before relying
on the feed operationally; this iteration does not place trades.
