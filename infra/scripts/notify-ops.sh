#!/usr/bin/env bash
# On-box: publish an ops alert to SNS. Invoked by the engine unit's systemd
# OnFailure hook (market-sentinel-alert@.service) when the engine crash-loops
# into a failed state. Runs as root; the box's instance role grants sns:Publish
# scoped to this one topic, so no keys are stored anywhere. Best-effort: never
# let a notification failure cascade (always exits 0).
set -uo pipefail

source /etc/market-sentinel/deploy.env

UNIT="${1:-market-sentinel.service}"

if [ -z "${SNS_TOPIC_ARN:-}" ]; then
  echo "no SNS_TOPIC_ARN in deploy.env; skipping ops alert" >&2
  exit 0
fi

host=$(hostname)
# Last few log lines give the on-call a head start without opening a session.
recent=$(systemctl status "$UNIT" --no-pager --lines=15 2>&1 | tail -n 15 || true)

aws sns publish \
  --region "$AWS_REGION" \
  --topic-arn "$SNS_TOPIC_ARN" \
  --subject "market-sentinel: $UNIT failed on $host" \
  --message "The unit '$UNIT' entered a failed state (repeated restarts / crash loop) on $host ($PROJECT).

Recent status:
$recent

Investigate: aws ssm start-session --target \$(instance) --region $AWS_REGION" \
  || echo "sns publish failed (non-fatal)" >&2

exit 0
