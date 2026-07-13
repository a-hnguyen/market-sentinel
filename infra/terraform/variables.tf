variable "project" {
  description = "Name prefix for all resources and tags."
  type        = string
  default     = "market-sentinel"
}

variable "region" {
  description = "AWS region. us-east-1 keeps the free-tier story simple."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = <<-EOT
    EC2 size. t3.micro is the tested x86 default for this low-throughput service.
    Credits and free-tier eligibility vary by account/program; verify pricing
    before changing the instance or relying on a discount.
  EOT
  type        = string
  default     = "t3.micro"
}

variable "ami_id" {
  description = <<-EOT
    AMI for the engine box. Leave empty to auto-resolve the latest Amazon Linux
    2023 x86_64 via SSM (see ec2.tf). Set explicitly to pin a known-good image.
  EOT
  type        = string
  default     = ""
}

variable "repo_url" {
  description = "Git repository the box clones on first boot. Private repos use the SSM GitHub token."
  type        = string
  default     = "https://github.com/a-hnguyen/market-sentinel.git"
}

variable "repo_branch" {
  description = "Branch to deploy."
  type        = string
  default     = "main"
}

variable "github_repo" {
  description = "OWNER/REPO the CI deploy role trusts via OIDC."
  type        = string
  default     = "a-hnguyen/market-sentinel"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if the account already has one (only one per account is allowed)."
  type        = bool
  default     = true
}

variable "ops_email" {
  description = <<-EOT
    Email that receives ops alerts (engine down, alarm). Left empty means no SNS
    subscription is created — set it to get notified. This is NOT the trading
    alert/control channel (Discord); this is infra health only.
  EOT
  type        = string
  default     = ""
}

variable "tags" {
  description = "Extra tags merged onto every resource."
  type        = map(string)
  default     = {}
}
