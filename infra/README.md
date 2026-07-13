# infra/ — AWS deploy for market-sentinel

Infrastructure-as-code for the **lean single-box** deploy (Shape 1 in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md)): one always-on EC2 instance runs the
alert engine as a systemd service; the weekday pre-market screen is triggered by
EventBridge → Lambda → SSM Run Command (no on-box timer). Everything is
provisioned with Terraform; the box is administered through SSM Session Manager
(no SSH, no open inbound ports).

> The engine core never changes for the cloud. Deploy touches only wiring +
> Notifier/Sink impls, per the four-seams contract.

## Layout

```
infra/
  terraform/            # all resources (one file per concern)
    versions.tf         # provider + terraform version pins
    backend.tf          # S3 remote state (native lockfile, no DynamoDB)
    variables.tf        # inputs (region, instance_type, ops_email, ...)
    main.tf             # provider, default-VPC data, common tags
    network.tf          # security group: NO inbound, egress only
    iam.tf              # least-priv instance role + profile
    s3.tf               # private overlay bucket; archive prefix reserved
    ssm.tf              # encrypted runtime config slots (set out-of-band)
    cloudwatch.tf       # reserved engine log group + EC2 status alarm
    sns.tf              # shared ops topic and optional email subscription
    lambda.tf           # thin pre-screen trigger
    eventbridge.tf      # weekday 10:00 UTC schedule
    github_oidc.tf      # keyless CI deploy role
    resource_group.tf   # one tagged AWS console overview
    ec2.tf              # Amazon Linux engine box
    user_data.sh.tftpl  # first-boot clone/install
    outputs.tf
    terraform.tfvars.example
  scripts/
    bootstrap-state.sh  # one-time: create state bucket (S3-native lockfile)
    fetch-config.sh     # SSM parameters → protected EnvironmentFile
    sync-overlay.sh     # private S3 inputs → git-ignored paths
    redeploy.sh         # CI/manual pull, install, restart
  systemd/              # engine, config, pre-screen, and failure notifier units
  lambda/               # EventBridge pre-screen trigger handler
```

## Services used (and why)

| Service | Role | Kept minimal? |
|---|---|---|
| **EC2** | Always-on box running the engine (persistent Alpaca websocket) | core |
| **IAM** | Least-priv instance role; no static keys anywhere | core |
| **SSM** | Parameter Store (secrets), Session Manager (shell), Run Command | core |
| **S3** | Private strategy overlay; lifecycle-ready archive prefix (unused today) | core |
| **CloudWatch** | EC2 status alarm, Lambda logs, and a reserved engine log group | core |
| **SNS** | Infra-health alerts (trading alerts/control use Discord) | minimal |
| **Lambda + EventBridge** | Thin weekday pre-market trigger → on-box pre-screen via Run Command | minimal |
| **GitHub Actions (OIDC)** | Tests on push/PR; deploy to box on main via a tag-scoped SSM role — no stored AWS keys | free |
| **Resource Groups** | One console view of every `Project`-tagged resource | free |

No RDS (no managed Postgres) — local disk holds `candidates.csv`, `alerts.log`,
and the manual watchlist. Engine stdout/stderr currently stays in journald. The
Terraform engine log group exists, but no CloudWatch agent currently ships the
journal into it.

## First-time setup

```bash
# 0. Configure AWS credentials for the target account (AWS SSO is preferred).

# 1. Create the remote-state bucket (once). Locking is S3-native (use_lockfile),
#    so there's no DynamoDB table to create.
cd infra/scripts && ./bootstrap-state.sh      # prints the init command

# 2. Init Terraform against that backend (command printed by step 1).
cd ../terraform
terraform init -backend-config="bucket=..." -backend-config="region=us-east-1"

# 3. Create the runtime-config slots + overlay bucket FIRST (not the box yet). The box
#    reads these at boot, so they must exist before it launches, or its clone
#    fails against SET_ME placeholders.
terraform apply -target=aws_ssm_parameter.config -target=aws_s3_bucket.overlay

# 4. Set the real secrets/IDs into the SSM slots.
aws ssm put-parameter --name /market-sentinel/alpaca_api_key    --type SecureString --value 'PK...' --overwrite
aws ssm put-parameter --name /market-sentinel/alpaca_secret_key --type SecureString --value '...'   --overwrite
aws ssm put-parameter --name /market-sentinel/discord_bot_token        --type SecureString --value '...' --overwrite
aws ssm put-parameter --name /market-sentinel/discord_guild_id         --type SecureString --value '...' --overwrite
aws ssm put-parameter --name /market-sentinel/discord_channel_id       --type SecureString --value '...' --overwrite
aws ssm put-parameter --name /market-sentinel/discord_allowed_user_ids --type SecureString --value '111...,222...' --overwrite
# Private repo only: a fine-grained GitHub PAT (Contents:read on THIS repo) so the
# box can clone. Skip this and make the repo public to clone without a token.
aws ssm put-parameter --name /market-sentinel/github_token      --type SecureString --value 'github_pat_...' --overwrite

# 5. Upload the private overlay (never in git) to the overlay bucket.
BUCKET=$(terraform output -raw overlay_bucket)
aws s3 cp ../../alertengine/settings_local.py        "s3://$BUCKET/private/settings_local.py"
aws s3 cp ../../alertengine/data/watchlist.xls        "s3://$BUCKET/private/watchlist.xls"
# Optional only after a private rule package exists:
# aws s3 cp --recursive ../../alertengine/rules/_private "s3://$BUCKET/private/rules/_private"

# 6. Now apply the rest — this launches the box, which boots with real secrets,
#    clones, installs the systemd units, and starts the engine.
terraform plan
terraform apply

# 7. Confirm it came up (no SSH — SSM Session Manager). Needs the session plugin.
eval "$(terraform output -raw ssm_session_command)"
#   on the box:  systemctl status market-sentinel   journalctl -u market-sentinel -f
```

## Scheduled pre-screen

EventBridge uses `cron(0 10 ? * MON-FRI *)`: 10:00 UTC, which is 02:00 PST or
03:00 PDT. Lambda skips its configured market-holiday dates and sends an SSM Run
Command targeting the EC2 `Project=market-sentinel` tag. The on-box command
checks Alpaca's calendar again, runs `market-sentinel-prescreen.service`, writes
`candidates.csv`, and restarts the engine so the new candidates are loaded.

Inspect or trigger it manually:

```bash
terraform output -raw prescreen_schedule
aws lambda invoke \
  --function-name "$(terraform output -raw prescreen_trigger_lambda)" \
  /tmp/prescreen-response.json
cat /tmp/prescreen-response.json
```

## Operations and logs

Open an interactive SSM shell from `infra/terraform`:

```bash
eval "$(terraform output -raw ssm_session_command)"
sudo systemctl status market-sentinel.service
sudo journalctl -u market-sentinel.service -n 200 --no-pager
sudo journalctl -u market-sentinel-prescreen.service -n 200 --no-pager
```

Useful service operations on the box:

```bash
sudo systemctl restart market-sentinel.service
sudo systemctl start market-sentinel-prescreen.service
sudo systemctl restart market-sentinel-config.service
```

The AWS console has Lambda logs and the EC2 status alarm. It does **not**
currently have engine journal entries; use SSM + `journalctl` until a CloudWatch
agent is intentionally added. `terraform output -raw console_overview` opens the
tag-based resource overview.

Pushes to `main` run Black and pytest in GitHub Actions, then assume the deploy
role through OIDC and invoke `infra/scripts/redeploy.sh` through SSM. That script
hard-resets the on-box checkout to `origin/main`, reinstalls the package, refreshes
the private config/overlay, and restarts the engine.

## Cost

The default cost driver is the always-on `t3.micro` plus its 20 GB gp3 volume;
the low-volume S3, Lambda, EventBridge, SNS, SSM, and CloudWatch resources are
usually much smaller. Free-tier eligibility and account credits vary by account
and AWS program, so confirm the current estimate in AWS Pricing Calculator and
set a billing budget instead of relying on a fixed number in this document.

## Safety

- **No inbound ports.** Shell access is SSM Session Manager only.
- **No static AWS credentials.** The box uses an instance role; CI uses OIDC.
  There is no IAM user with long-lived AWS keys. Third-party API tokens remain
  encrypted in Parameter Store and are scoped by the instance-role policy.
- **Strategy IP never in git.** The overlay bucket is fully private and is how
  the real strategy reaches the box; a public `git clone` is intentionally
  incomplete.
