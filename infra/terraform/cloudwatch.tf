# Log group for the engine's stdout/journald (shipped by the CloudWatch agent,
# configured in user_data). Defined in the foundation because the instance role
# policy scopes log writes to this exact group. Retention keeps cost near-zero.
#
resource "aws_cloudwatch_log_group" "engine" {
  name              = "/${local.name}/engine"
  retention_in_days = 14

  tags = { Name = "${local.name}-engine" }
}

# Box-level paging: fire when the instance fails its EC2 status checks (host
# failure, kernel hang, network unreachable) or disappears entirely. Basic
# monitoring is free (5-min granularity). App-level crash loops are caught
# separately by the engine unit's systemd OnFailure hook, which publishes to the
# same SNS topic. treat_missing_data=breaching so a terminated/stopped box also
# pages (metric stops reporting when the instance is gone).
resource "aws_cloudwatch_metric_alarm" "instance_status" {
  alarm_name          = "${local.name}-instance-status-failed"
  alarm_description   = "Engine box failed EC2 status checks or went unreachable."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"

  dimensions    = { InstanceId = aws_instance.engine.id }
  alarm_actions = [aws_sns_topic.ops.arn]
  ok_actions    = [aws_sns_topic.ops.arn]

  tags = { Name = "${local.name}-instance-status" }
}
