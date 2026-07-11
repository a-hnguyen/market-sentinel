# Remote state in S3 with native S3 lockfile locking (use_lockfile). State is
# remote (not on the laptop) so CI and local runs share one source of truth and
# can't race; the lock is a short-lived object S3 writes conditionally, so no
# DynamoDB table is needed (that approach is deprecated in AWS provider v5.x).
#
# Chicken-and-egg: the bucket must exist BEFORE `terraform init` can use it.
# Create it once with infra/scripts/bootstrap-state.sh, then init.
#
# The bucket name is supplied at init time (it embeds the account id):
#   terraform init -backend-config="bucket=<your-state-bucket>"
terraform {
  backend "s3" {
    key          = "market-sentinel/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
    # bucket supplied via -backend-config (see scripts/bootstrap-state.sh)
  }
}
