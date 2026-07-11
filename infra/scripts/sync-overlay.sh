#!/usr/bin/env bash
# On-box: pull the git-ignored private strategy overlay from the S3 overlay
# bucket into the cloned repo. These files are NEVER in git — this is how the
# real strategy reaches the box. Runs as root, then hands ownership to the app
# user. Invoked by the config unit. Missing objects are tolerated so the box
# still boots (engine runs on the public textbook rule until the overlay lands).
set -euo pipefail

source /etc/market-sentinel/deploy.env

S3="s3://$OVERLAY_BUCKET/private"
AE="$APP_DIR/alertengine"

# Real tuned params (overrides settings.py at import).
aws s3 cp "$S3/settings_local.py" "$AE/settings_local.py" \
  --region "$AWS_REGION" || echo "no settings_local.py in overlay (using defaults)"

# Curated watchlist for the pre-screen.
aws s3 cp "$S3/watchlist.xls" "$AE/data/watchlist.xls" \
  --region "$AWS_REGION" || echo "no watchlist.xls in overlay (prescreen will skip)"

# Private rule package (the real IP). Sync the whole dir if present.
if aws s3 ls "$S3/rules/_private/" --region "$AWS_REGION" >/dev/null 2>&1; then
  mkdir -p "$AE/rules/_private"
  aws s3 sync "$S3/rules/_private/" "$AE/rules/_private/" --region "$AWS_REGION"
else
  echo "no rules/_private in overlay (using public rule)"
fi

chown -R "$APP_USER":"$APP_USER" "$AE"
echo "overlay sync complete"
