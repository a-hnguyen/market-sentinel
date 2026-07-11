# infra/ — AWS deploy for market-sentinel

Infrastructure-as-code for the **lean single-box** deploy (Shape 1 in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md)): one always-on EC2 instance runs the
alert engine as a systemd service, with a nightly pre-screen timer. Everything is
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
    s3.tf               # private overlay bucket (strategy IP, archives)
    ssm.tf              # SecureString param slots (values set out-of-band)
    cloudwatch.tf       # engine log group  (alarm added in Phase 3)
    outputs.tf
    terraform.tfvars.example
  scripts/
    bootstrap-state.sh  # one-time: create the state bucket + lock table
  # added in later phases:
  #   terraform/ec2.tf, user_data.sh.tftpl   (Phase 2)
  #   systemd/*.service, *.timer             (Phase 2)
  #   terraform/sns.tf + alarm               (Phase 3)
  #   lambda/prescreen_trigger/, lambda.tf   (Phase 4)
  #   ../.github/workflows/deploy.yml        (Phase 5)
```

## Services used (and why)

| Service | Role | Kept minimal? |
|---|---|---|
| **EC2** | Always-on box running the engine (persistent Alpaca websocket) | core |
| **IAM** | Least-priv instance role; no static keys anywhere | core |
| **SSM** | Parameter Store (secrets), Session Manager (shell), Run Command | core |
| **S3** | Private overlay (strategy IP) + candidate/alert archives | core |
| **CloudWatch** | Engine logs + a single "engine down" alarm | core |
| **SNS** | Infra-health alerts (not the trading channel — that's ntfy) | minimal |
| **Lambda + EventBridge** | Thin nightly trigger → on-box pre-screen via Run Command | minimal |

No RDS (no managed Postgres) — local disk holds `candidates.csv`/`alerts.log`, so
the single-box shape stays at ~free-tier cost.

## First-time setup

```bash
# 0. Configure AWS creds for the NEW account (aws configure / SSO).

# 1. Create the remote-state bucket (once). Locking is S3-native (use_lockfile),
#    so there's no DynamoDB table to create.
cd infra/scripts && ./bootstrap-state.sh      # prints the init command

# 2. Init Terraform against that backend (command printed by step 1).
cd ../terraform
terraform init -backend-config="bucket=..." -backend-config="region=us-east-1"

# 3. Review + apply.
terraform plan
terraform apply

# 4. Set the real secrets into the SSM slots Terraform created.
aws ssm put-parameter --name /market-sentinel/alpaca_api_key    --type SecureString --value 'PK...' --overwrite
aws ssm put-parameter --name /market-sentinel/alpaca_secret_key --type SecureString --value '...'   --overwrite
aws ssm put-parameter --name /market-sentinel/ntfy_topic        --type SecureString --value '...'   --overwrite

# 5. Upload the private overlay (never in git) to the overlay bucket.
BUCKET=$(terraform output -raw overlay_bucket)
aws s3 cp ../../alertengine/settings_local.py       "s3://$BUCKET/private/settings_local.py"
aws s3 cp ../../alertengine/data/watchlist.xls       "s3://$BUCKET/private/watchlist.xls"
aws s3 cp --recursive ../../alertengine/rules/_private "s3://$BUCKET/private/rules/_private"
```

## Cost

Free-tier (new account, first 12 months): ~$0–3/mo. After: ~$13/mo (mostly the
`t3.micro` + EBS). SNS/Lambda/CloudWatch/S3 round to cents at this volume.

## Safety

- **No inbound ports.** Shell access is SSM Session Manager only.
- **No static credentials.** The box uses an instance role; CI (Phase 5) uses
  OIDC. There is no IAM user with long-lived keys.
- **Strategy IP never in git.** The overlay bucket is fully private and is how
  the real strategy reaches the box; a public `git clone` is intentionally
  incomplete.
