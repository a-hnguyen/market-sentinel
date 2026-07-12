# Ops alerting channel. One SNS topic that BOTH paths publish to:
#   - box-level: the CloudWatch StatusCheckFailed alarm (cloudwatch.tf)
#   - app-level: the engine unit's systemd OnFailure hook (crash loop) via the
#     box's instance role (sns:Publish is scoped to this topic in iam.tf)
# The email subscription is created only when var.ops_email is set; AWS then
# emails a confirmation link you must click before anything is delivered.
resource "aws_sns_topic" "ops" {
  name = "${local.name}-ops"
  tags = { Name = "${local.name}-ops" }
}

resource "aws_sns_topic_subscription" "ops_email" {
  count     = var.ops_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.ops.arn
  protocol  = "email"
  endpoint  = var.ops_email
}
