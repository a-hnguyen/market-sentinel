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
ntfy_topic=$(get ntfy_topic)

{
  echo "ALPACA_API_KEY=$api_key"
  echo "ALPACA_SECRET_KEY=$secret_key"
  # Only enable ntfy push when a real topic is set (placeholder = console-only).
  if [ "$ntfy_topic" != "SET_ME" ] && [ -n "$ntfy_topic" ]; then
    echo "NTFY_TOPIC=$ntfy_topic"
  fi
} > "$ENV_FILE"

# Readable only by the service user that runs the engine.
chown "$APP_USER":"$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "wrote $ENV_FILE"
