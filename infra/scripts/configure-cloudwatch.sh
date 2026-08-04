#!/usr/bin/env bash
# Install and configure the CloudWatch agent to ship bounded application logs.
# Idempotent: safe during first boot and every CI redeploy.
set -euo pipefail

source /etc/market-sentinel/deploy.env

LOG_DIR=/var/log/market-sentinel
CONFIG=/opt/aws/amazon-cloudwatch-agent/etc/market-sentinel.json

if ! rpm -q amazon-cloudwatch-agent >/dev/null 2>&1; then
  dnf install -y amazon-cloudwatch-agent
fi

install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$LOG_DIR"
install -d -m 0755 "$(dirname "$CONFIG")"

cat > "$CONFIG" <<EOF
{
  "agent": {
    "region": "$AWS_REGION",
    "run_as_user": "root"
  },
  "logs": {
    "force_flush_interval": 5,
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "$LOG_DIR/engine.log*",
            "log_group_name": "$LOG_GROUP",
            "log_stream_name": "{instance_id}/engine",
            "timezone": "UTC"
          },
          {
            "file_path": "$LOG_DIR/prescreen.log*",
            "log_group_name": "$LOG_GROUP",
            "log_stream_name": "{instance_id}/prescreen",
            "timezone": "UTC"
          }
        ]
      }
    }
  }
}
EOF

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -s -c "file:$CONFIG"
