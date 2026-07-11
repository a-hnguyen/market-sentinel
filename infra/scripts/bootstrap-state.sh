#!/usr/bin/env bash
# One-time bootstrap: create the S3 bucket that holds Terraform's remote state.
# Run this ONCE, before the first `terraform init`, after your AWS credentials
# are configured (aws configure / SSO).
#
# The state backend can't manage the bucket that stores its own state (chicken
# and egg), so it's created out-of-band with the CLI here. Locking uses S3's
# native lockfile (use_lockfile in backend.tf) — no DynamoDB table needed.
#
# Usage:
#   ./bootstrap-state.sh            # uses defaults below
#   REGION=us-west-2 ./bootstrap-state.sh
set -euo pipefail

REGION="${REGION:-us-east-1}"
PROJECT="${PROJECT:-market-sentinel}"
# Bucket names are global; add the account id to keep it unique.
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${PROJECT}-tfstate-${ACCOUNT_ID}"

echo "Region:  $REGION"
echo "Bucket:  $BUCKET"
echo

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "State bucket already exists — skipping create."
else
  echo "Creating state bucket..."
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
  aws s3api put-bucket-versioning --bucket "$BUCKET" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

echo
echo "Done. Now initialize Terraform with:"
echo
echo "  cd infra/terraform"
echo "  terraform init \\"
echo "    -backend-config=\"bucket=$BUCKET\" \\"
echo "    -backend-config=\"region=$REGION\""
