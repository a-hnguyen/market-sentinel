# Thin pre-screen trigger Lambda: EventBridge invokes it weekday pre-market, and
# it asks SSM to start the pre-screen unit on the box (eventbridge.tf wires the
# schedule). Pure-Python + boto3 (in the runtime), so no layer/deps — Terraform
# zips the single handler file directly.

data "archive_file" "prescreen_trigger" {
  type        = "zip"
  source_file = "${path.module}/../lambda/prescreen_trigger/handler.py"
  output_path = "${path.module}/.build/prescreen_trigger.zip"
}

resource "aws_cloudwatch_log_group" "prescreen_trigger" {
  name              = "/aws/lambda/${local.name}-prescreen-trigger"
  retention_in_days = 14
  tags              = { Name = "${local.name}-prescreen-trigger" }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "prescreen_trigger" {
  name_prefix        = "${local.name}-presched-"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { Name = "${local.name}-prescreen-trigger" }
}

# Least-privilege: write only its own logs, and SendCommand only the shell doc,
# only to instances tagged Project=<project>. No broad ssm:* or ec2:* anywhere.
data "aws_iam_policy_document" "prescreen_trigger" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.prescreen_trigger.arn}:*"]
  }

  statement {
    sid       = "SendCommandDocument"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${var.region}::document/AWS-RunShellScript"]
  }

  statement {
    sid       = "SendCommandInstances"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Project"
      values   = [var.project]
    }
  }
}

resource "aws_iam_role_policy" "prescreen_trigger" {
  name   = "${local.name}-prescreen-trigger"
  role   = aws_iam_role.prescreen_trigger.id
  policy = data.aws_iam_policy_document.prescreen_trigger.json
}

resource "aws_lambda_function" "prescreen_trigger" {
  function_name    = "${local.name}-prescreen-trigger"
  role             = aws_iam_role.prescreen_trigger.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.prescreen_trigger.output_path
  source_code_hash = data.archive_file.prescreen_trigger.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      PROJECT_TAG    = var.project
      PRESCREEN_UNIT = "market-sentinel-prescreen.service"
    }
  }

  # Ensure the log group (with our retention) exists before first invoke, instead
  # of Lambda auto-creating one with never-expire retention.
  depends_on = [aws_cloudwatch_log_group.prescreen_trigger]

  tags = { Name = "${local.name}-prescreen-trigger" }
}
