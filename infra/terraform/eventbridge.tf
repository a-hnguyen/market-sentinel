# EventBridge Scheduler runs the post-close pre-screen at 3:00 PM Pacific on
# weekdays. Unlike a legacy EventBridge Rule cron, Scheduler accepts an IANA
# timezone and therefore keeps the local time stable across PST/PDT changes.

data "aws_iam_policy_document" "prescreen_scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "prescreen_scheduler" {
  name_prefix        = "${local.name}-scheduler-"
  assume_role_policy = data.aws_iam_policy_document.prescreen_scheduler_assume.json
  tags               = { Name = "${local.name}-prescreen-scheduler" }
}

data "aws_iam_policy_document" "prescreen_scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.prescreen_trigger.arn]
  }
}

resource "aws_iam_role_policy" "prescreen_scheduler" {
  name   = "${local.name}-prescreen-scheduler"
  role   = aws_iam_role.prescreen_scheduler.id
  policy = data.aws_iam_policy_document.prescreen_scheduler.json
}

resource "aws_scheduler_schedule" "prescreen" {
  name                         = "${local.name}-prescreen"
  description                  = "Weekday 3:00 PM Pacific post-close pre-screen."
  schedule_expression          = "cron(0 15 ? * MON-FRI *)"
  schedule_expression_timezone = "America/Los_Angeles"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.prescreen_trigger.arn
    role_arn = aws_iam_role.prescreen_scheduler.arn
  }
}
