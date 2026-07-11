# Security group with NO inbound rules. The box is administered entirely through
# SSM Session Manager (agent dials out to AWS), so there is no SSH port open to
# the internet — nothing to brute-force, no key to leak. Egress is open so the
# engine can reach Alpaca, ntfy, S3, and the SSM endpoints.
resource "aws_security_group" "engine" {
  name_prefix = "${local.name}-engine-"
  description = "market-sentinel engine: no inbound, all egress (SSM-managed host)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound (Alpaca WS, ntfy, S3, SSM, package installs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-engine" }

  lifecycle {
    create_before_destroy = true
  }
}
