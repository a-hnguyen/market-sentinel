# Provider + shared data sources + common tags. Resource definitions live in
# their own files (network.tf, iam.tf, ssm.tf, s3.tf, ec2.tf, ...).

provider "aws" {
  region = var.region

  default_tags {
    tags = merge(
      {
        Project   = var.project
        ManagedBy = "terraform"
      },
      var.tags,
    )
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Use the account's default VPC + its subnets. The engine only makes OUTBOUND
# connections (Alpaca/Discord websockets, S3, SSM), so it needs no custom network —
# the default VPC's public subnets with a no-inbound security group are enough.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  name = var.project
}
