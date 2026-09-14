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
aws s3 cp "$S3/watchlist.xlsx" "$AE/data/watchlist.xlsx" \
  --region "$AWS_REGION" || echo "no watchlist.xlsx in overlay (prescreen will skip)"

# User-managed symbols must survive EC2 replacements. Runtime writes upload the
# same object after every /watch or /unwatch; a missing first-boot object is fine.
aws s3 cp "$S3/runtime/manual_watchlist.txt" "$AE/data/manual_watchlist.txt" \
  --region "$AWS_REGION" || echo "no persisted manual watchlist in overlay"
aws s3 cp "$S3/runtime/active_strategy.txt" "$AE/data/active_strategy.txt" \
  --region "$AWS_REGION" || echo "no persisted active strategy in overlay"

# The overnight watcher has an independent watchlist and refreshable Robinhood
# OAuth state. Both remain private and survive instance replacement.
aws s3 cp "$S3/runtime/penny_watchlist.txt" "$AE/data/penny_watchlist.txt" \
  --region "$AWS_REGION" || echo "no persisted penny watchlist in overlay"
aws s3 cp "$S3/runtime/robinhood_oauth.json" "$AE/data/robinhood_oauth.json" \
  --region "$AWS_REGION" || echo "no Robinhood OAuth state in overlay"
if [[ -f "$AE/data/robinhood_oauth.json" ]]; then
  chmod 600 "$AE/data/robinhood_oauth.json"
fi

# Private rule package (the real IP). Sync the whole dir if present.
if aws s3 ls "$S3/rules/_private/" --region "$AWS_REGION" >/dev/null 2>&1; then
  mkdir -p "$AE/rules/_private"
  aws s3 sync "$S3/rules/_private/" "$AE/rules/_private/" --region "$AWS_REGION"
else
  echo "no rules/_private in overlay (using public rule)"
fi

chown -R "$APP_USER":"$APP_USER" "$AE"
echo "overlay sync complete"
