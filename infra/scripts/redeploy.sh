#!/usr/bin/env bash
# On-box redeploy: pull the latest branch, reinstall deps + systemd units, and
# restart the engine. Invoked by CI (GitHub Actions → SSM Run Command) after
# tests pass, and safe to run by hand. Idempotent.
#
# Self-update safety: `git reset --hard` rewrites THIS file mid-run, which would
# corrupt a bash process still reading it. So the first pass pulls, then re-execs
# the now-updated copy (guarded by a sentinel env var) which does the real work.
set -euo pipefail

source /etc/market-sentinel/deploy.env
BRANCH="${1:-${repo_branch:-main}}"

if [ -z "${REDEPLOY_REEXEC:-}" ]; then
  # --- pass 1: pull latest, then hand off to the fresh script ---------------
  # Authenticate the fetch with the read-only GH token from SSM (private repo),
  # same pattern as first-boot user_data — the token never lands in git config.
  GH_TOKEN=$(aws ssm get-parameter --name "$SSM_PREFIX/github_token" \
    --with-decryption --query Parameter.Value --output text --region "$AWS_REGION")
  AUTH_HDR="Authorization: Basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"

  sudo -u "$APP_USER" git -C "$APP_DIR" -c "http.extraHeader=$AUTH_HDR" fetch --depth 1 origin "$BRANCH"
  sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard "origin/$BRANCH"
  unset GH_TOKEN AUTH_HDR

  REDEPLOY_REEXEC=1 exec "$APP_DIR/infra/scripts/redeploy.sh" "$BRANCH"
fi

# --- pass 2: running the freshly-pulled code ------------------------------
# Reinstall in case pyproject deps changed (editable install already reflects
# code edits, but new dependencies need pip to resolve them).
sudo -u "$APP_USER" "$VENV/bin/pip" install -e "$APP_DIR" -q

# Reinstall units (they may have changed) and restart. Only *.service — the
# schedule lives in EventBridge, not an on-box timer.
install -m 0644 "$APP_DIR"/infra/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart market-sentinel-config.service
systemctl restart market-sentinel.service

echo "redeployed to origin/$BRANCH @ $(sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse --short HEAD)"
