#!/usr/bin/env bash
# On-box: read the SSM SecureString params into an EnvironmentFile the engine +
# prescreen units load. Runs as root (writes /etc); the box authenticates with
# its instance role, so no keys are stored anywhere. Invoked by the config unit.
set -euo pipefail

source /etc/market-sentinel/deploy.env

ENV_FILE=/etc/market-sentinel/engine.env
umask 077

get() {
  aws ssm get-parameter --name "$SSM_PREFIX/$1" --with-decryption \
    --query Parameter.Value --output text --region "$AWS_REGION"
}

api_key=$(get alpaca_api_key)
secret_key=$(get alpaca_secret_key)
discord_bot_token=$(get discord_bot_token)
discord_guild_id=$(get discord_guild_id)
discord_channel_id=$(get discord_channel_id)
discord_allowed_user_ids=$(get discord_allowed_user_ids)

{
  echo "ALPACA_API_KEY=$api_key"
  echo "ALPACA_SECRET_KEY=$secret_key"
  echo "DISCORD_BOT_TOKEN=$discord_bot_token"
  echo "DISCORD_GUILD_ID=$discord_guild_id"
  echo "DISCORD_CHANNEL_ID=$discord_channel_id"
  echo "DISCORD_ALLOWED_USER_IDS=$discord_allowed_user_ids"
} > "$ENV_FILE"

# Readable only by the service user that runs the engine.
chown "$APP_USER":"$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "wrote $ENV_FILE"
