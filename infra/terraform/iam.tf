# Instance role for the engine box. Least-privilege: the box can read ONLY its
# own SSM parameters, read/write ONLY its own overlay bucket, ship logs, and be
# managed by Session Manager. No static IAM user, no long-lived keys anywhere —
# the instance profile hands the box short-lived credentials automatically.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "engine" {
  name_prefix        = "${local.name}-engine-"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = { Name = "${local.name}-engine" }
}

# Session Manager (browser/CLI shell, no SSH) + Run Command target. AWS-managed
# policy; this is the standard "let SSM manage this host" grant.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.engine.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Scoped inline policy: only this project's parameters, only this bucket.
data "aws_iam_policy_document" "engine" {
  statement {
    sid     = "ReadOwnSsmParams"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = [
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}/*",
    ]
  }

  statement {
    sid     = "DecryptSsmParams"
    actions = ["kms:Decrypt"]
    resources = [
      "arn:aws:kms:${var.region}:${data.aws_caller_identity.current.account_id}:alias/aws/ssm",
    ]
  }

  statement {
    sid       = "OverlayBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.overlay.arn]
  }

  statement {
    sid       = "OverlayObjectRW"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.overlay.arn}/*"]
  }

  statement {
    # Reserved for a future CloudWatch agent; no engine log shipper is installed
    # in the current user-data bootstrap.
    sid = "ShipLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.engine.arn}:*"]
  }

  # Only the ops topic, only publish — the systemd OnFailure hook uses this to
  # page when the engine unit crash-loops into a failed state.
  statement {
    sid       = "PublishOpsAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops.arn]
  }
}

resource "aws_iam_role_policy" "engine" {
  name   = "${local.name}-engine"
  role   = aws_iam_role.engine.id
  policy = data.aws_iam_policy_document.engine.json
}

resource "aws_iam_instance_profile" "engine" {
  name_prefix = "${local.name}-engine-"
  role        = aws_iam_role.engine.name
}
