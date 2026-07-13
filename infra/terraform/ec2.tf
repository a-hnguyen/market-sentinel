# The engine box. One always-on instance holds the persistent Alpaca websocket
# and runs the engine as a systemd service. EventBridge/Lambda starts the scheduled
# pre-screen through SSM. It reaches the internet outbound only (no inbound;
# admin is SSM Session Manager), assumes the least-priv instance role, and pulls its secrets
# (SSM) and private overlay (S3) itself on boot.

# Latest Amazon Linux 2023 x86_64, resolved from the public SSM parameter AWS
# publishes — so we track a patched image without hardcoding an AMI id. Override
# with var.ami_id to pin.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "engine" {
  ami                    = var.ami_id != "" ? var.ami_id : data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.engine.name
  vpc_security_group_ids = [aws_security_group.engine.id]
  subnet_id              = data.aws_subnets.default.ids[0]

  # Default subnets are public; the box needs a public IP for outbound egress
  # (Alpaca/Discord/S3/SSM) via the internet gateway. Nothing listens inbound.
  associate_public_ip_address = true

  # Bootstrap: clone repo, write deploy.env, install units, enable services.
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    project        = var.project
    region         = var.region
    repo_url       = var.repo_url
    repo_branch    = var.repo_branch
    overlay_bucket = aws_s3_bucket.overlay.id
    ssm_prefix     = local.ssm_prefix
    log_group      = aws_cloudwatch_log_group.engine.name
    sns_topic_arn  = aws_sns_topic.ops.arn
  })
  # Re-run bootstrap when the script or its inputs change (replaces the box).
  user_data_replace_on_change = true

  root_block_device {
    volume_size = 20 # GB — free tier covers 30 GB of gp3
    volume_type = "gp3"
    encrypted   = true
  }

  # Require IMDSv2 (token-based metadata) — blocks the SSRF-style creds theft
  # that plagued IMDSv1.
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = { Name = "${local.name}-engine" }
}
