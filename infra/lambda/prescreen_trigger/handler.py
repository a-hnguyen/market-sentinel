"""EventBridge Scheduler trigger for the post-close pre-screen.

EventBridge Scheduler fires this weekday at 3:00 PM Pacific (see
eventbridge.tf). Rather than run
the screen itself (which would mean packaging pandas/yfinance/alpaca into the
Lambda), it stays thin: it decides *whether* today is a trading day, then asks
SSM to start the pre-screen systemd unit on the engine box. The heavy lifting
stays on the box where the code and the private overlay already live.

Why a Lambda at all, vs the old systemd timer: the schedule now lives in AWS
(visible/editable without SSH), and this function can gate on market holidays so
the box isn't even pinged on days the market is closed. The box is resolved by
tag, so a replaced instance (new id) is picked up automatically.
"""

import datetime
import os

import boto3

# NYSE full-day closures. EventBridge already restricts to Mon-Fri, so weekends
# need no handling here; this is the holiday overlay. Update yearly (the on-box
# pre-screen also self-skips non-trading days, so a stale list only costs one
# harmless no-op run, never a bad screen).
MARKET_HOLIDAYS = {
    d.strip()
    for d in os.environ.get(
        "MARKET_HOLIDAYS",
        # 2026 NYSE holidays
        "2026-01-01,2026-01-19,2026-02-16,2026-04-03,2026-05-25,"
        "2026-06-19,2026-07-03,2026-09-07,2026-11-26,2026-12-25",
    ).split(",")
    if d.strip()
}

PROJECT_TAG = os.environ.get("PROJECT_TAG", "market-sentinel")
PRESCREEN_UNIT = os.environ.get("PRESCREEN_UNIT", "market-sentinel-prescreen.service")
REGION = os.environ["AWS_REGION"]  # provided by the Lambda runtime


def lambda_handler(event, context):
    today = datetime.date.today()

    if today.weekday() >= 5:  # 5=Sat, 6=Sun — belt-and-suspenders vs the cron
        return _skip(f"{today} is a weekend")
    if today.isoformat() in MARKET_HOLIDAYS:
        return _skip(f"{today} is a market holiday")

    ssm = boto3.client("ssm", region_name=REGION)
    # Target by tag, not instance id, so a replaced box is picked up automatically.
    resp = ssm.send_command(
        Targets=[{"Key": "tag:Project", "Values": [PROJECT_TAG]}],
        DocumentName="AWS-RunShellScript",
        Comment="scheduled pre-screen trigger",
        Parameters={"commands": [f"systemctl start {PRESCREEN_UNIT}"]},
    )
    command_id = resp["Command"]["CommandId"]
    print(f"sent pre-screen command {command_id} to tag:Project={PROJECT_TAG}")
    return {"status": "triggered", "command_id": command_id, "date": today.isoformat()}


def _skip(reason):
    print(f"skipping pre-screen: {reason}")
    return {"status": "skipped", "reason": reason}
