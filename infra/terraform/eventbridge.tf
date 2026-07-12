# The schedule, now in AWS instead of a systemd timer on the box. 10:00 UTC is
# ~5-6am ET, safely before the 9:30 ET open. EventBridge cron is UTC and its
# day-of-week field is 1-indexed with '?' for the unused day-of-month slot.
resource "aws_cloudwatch_event_rule" "prescreen" {
  name                = "${local.name}-prescreen"
  description         = "Weekday pre-market trigger for the pre-screen Lambda."
  schedule_expression = "cron(0 10 ? * MON-FRI *)"
  tags                = { Name = "${local.name}-prescreen" }
}

resource "aws_cloudwatch_event_target" "prescreen" {
  rule = aws_cloudwatch_event_rule.prescreen.name
  arn  = aws_lambda_function.prescreen_trigger.arn
}

# Let EventBridge invoke the function (resource-based permission on the Lambda).
resource "aws_lambda_permission" "prescreen_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.prescreen_trigger.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.prescreen.arn
}
