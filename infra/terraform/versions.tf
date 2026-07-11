# Provider and Terraform version pins. Pinning keeps `terraform init` on any
# machine (laptop or CI) resolving to the same provider so plans are reproducible.
terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}
