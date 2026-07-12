# Discord setup

The production engine uses a private Discord bot as both its remote command
surface and its trading-alert channel. The bot connects outbound over Discord's
Gateway; the EC2 security group keeps zero inbound ports.

## 1. Create the private app

1. In the Discord Developer Portal, create an application and add a bot.
2. Reset/copy the bot token and store it immediately; never put it in git or chat.
3. Under OAuth2 URL Generator, select `bot` and `applications.commands`.
4. Grant only **View Channels**, **Send Messages**, and **Embed Links**, then use
   the generated URL to add the bot to the private server.

The bot uses slash commands, so it does **not** need the privileged Message
Content intent.

## 2. Create the channel and copy IDs

Create a private text channel visible only to the bot, Dad, and the maintainer.
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
  --type SecureString --value 'USER_ID,DAD_USER_ID' --overwrite
```

For local testing, put the same four names in the git-ignored `.env` instead.

## 4. Verify before market hours

Run replay mode locally or deploy the service, then use the private channel:

```text
/watch AAPL
/watchlist
/status AAPL
/unwatch AAPL
```

The bot should respond to each command and post buy/sell setup and confirmation
embeds. Test an unauthorized Discord account or another channel too; it must get
only an ephemeral denial.

Available commands: `/watch`, `/unwatch`, `/watchlist`, `/status`, `/screen`,
`/prescreen`, `/start`, `/stop confirm:true`, and `/help`.

