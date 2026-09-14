# Discord setup

The production engine uses a private Discord bot as both its remote command
surface and its trading-alert channel. The bot connects outbound over Discord's
Gateway; the EC2 security group keeps zero inbound ports.

## 1. Create the private app

1. In the Discord Developer Portal, create an application and add a bot.
2. Reset/copy the bot token and store it immediately; never put it in git or chat.
3. Under OAuth2 URL Generator, select `bot` and `applications.commands`.
4. Grant only **View Channels**, **Send Messages**, **Embed Links**, and **Attach
   Files**, then use the generated URL to add the bot to the private server.
   Attach Files is needed when a large `/status` response is returned as JSON.

The bot uses slash commands, so it does **not** need the privileged Message
Content intent.

## 2. Create the channel and copy IDs

Create a private text channel visible only to the bot and authorized users.
Enable Discord **Developer Mode**, then copy:

- server ID → `DISCORD_GUILD_ID`
- private channel ID → `DISCORD_CHANNEL_ID`
- each authorized person's user ID → comma-separated `DISCORD_ALLOWED_USER_IDS`

The application token is `DISCORD_BOT_TOKEN`.

## 3. Configure AWS

```bash
aws ssm put-parameter --name /market-sentinel/discord_bot_token \
  --type SecureString --value 'BOT_TOKEN' --overwrite
aws ssm put-parameter --name /market-sentinel/discord_guild_id \
  --type SecureString --value 'SERVER_ID' --overwrite
aws ssm put-parameter --name /market-sentinel/discord_channel_id \
  --type SecureString --value 'CHANNEL_ID' --overwrite
aws ssm put-parameter --name /market-sentinel/discord_allowed_user_ids \
  --type SecureString --value 'USER_ID,SECOND_USER_ID' --overwrite
```

After changing an existing SSM value, refresh the on-box environment and restart
the bot:

```bash
aws ssm send-command \
  --targets 'Key=tag:Project,Values=market-sentinel' \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl restart market-sentinel-config.service","systemctl restart market-sentinel.service"]'
```

For local testing, put the same four names in the git-ignored `.env` instead.

## 4. Verify the deployed service before market hours

Deploy/start the EC2 service, then use the private channel:

```text
/watch AAPL MSFT
/watchlist
/strategy
/status AAPL
/unwatch AAPL MSFT
```

The bot should respond to each command. Send a labeled synthetic embed during
initial setup (or verify the next real setup alert) to confirm Embed Links. Test
an unauthorized Discord account or another channel too; it must receive only an
ephemeral denial.

Alert delivery is event-driven rather than continuously refreshed:

- An armed setup creates one lifecycle card. Its relative expiration is rendered
  by Discord in each viewer's local time without the bot editing every minute.
- Confirmation edits the original card to `CONFIRMED` and posts a separate fresh
  BUY/SELL message so it follows Discord's normal new-message notification path.
- Timeout or alert-window closure edits the original card to `EXPIRED` and does
  not send a separate expiration message.
- Armed cards prioritize **Research on Yahoo**; confirmed alerts prioritize
  **Open in Robinhood** and retain Yahoo as the second link. Expired cards remove
  the brokerage link. Every link opens a stock-detail page only—no order is
  submitted or prefilled.
- Card handles are short-lived in-memory state. A service restart leaves any old
  card static, but its expiration remains visible and new setups create new
  cards.

Do not run a local `--headless` process with the production token while EC2 is
online: it would create a second Gateway client for the same control bot. Local
replay should use the normal console REPL (`python -m alertengine --replay`).

Available commands: `/watch`, `/unwatch`, `/watchlist`, `/status`, `/screen`,
`/prescreen`, `/strategy [NAME]`, `/start`, `/stop confirm:true`, and `/help`.

If the optional Robinhood watcher is enabled, the bot also registers
`/penny-watch`, `/penny-unwatch`, `/penny-watchlist`, `/penny-status`,
`/penny-start`, and `/penny-stop confirm:true`. These use an independent
persisted watchlist and do not affect the regular Alpaca watcher.

- `/watch STOCKS` accepts one or more space-separated symbols, persists the
  valid entries locally and to the private S3 overlay, reports invalid entries
  as skipped, and starts/restarts streaming once. If S3 fails, the local change
  still applies and Discord reports the persistence warning.
- `/unwatch STOCKS` accepts the same format and removes every supplied symbol
  that is valid from the current gate and manual-symbol file in one update. It
  reports invalid entries as skipped. A symbol remaining in `candidates.csv`
  can return after a restart.
- `/stop confirm:true` stops only market streaming; Discord stays online and
  `/start` resumes the existing watchlist.
- `/strategy` shows the active and available strategies. `/strategy NAME`
  switches the live watcher, clears incompatible bar/armed state, restarts the
  subscription, and persists the choice locally and to the private S3 overlay.
- `/prescreen` responds immediately, scans in a background child process, and
  posts separately labeled 4-hour, 1-hour, and final-intersection lists for
  oversold (`RSI < 30`) and overbought (`RSI > 70`) candidates, followed by the
  combined automatic watchlist and additions/removals. A second request is
  rejected while one is running.
- `/status` includes watcher state, active strategy, aggregation interval,
  per-symbol state, and whether the configured Pacific alert window is currently
  open. It also separates automatic pre-screen symbols from explicit manual
  `/watch` symbols.
- Alerts based on bars before the 6:30 AM Pacific regular-session open are
  prefixed `PREMARKET` so extended-hours signals are unambiguous.
- `/screen` makes each candidate symbol a Yahoo Finance research link.
