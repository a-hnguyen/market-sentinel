# Log group for the engine's stdout/journald (shipped by the CloudWatch agent,
# configured in user_data). Defined in the foundation because the instance role
# policy scopes log writes to this exact group. Retention keeps cost near-zero.
#
# The alarm + SNS action that page on "engine down" are added in Phase 3.
resource "aws_cloudwatch_log_group" "engine" {
  name              = "/${local.name}/engine"
  retention_in_days = 14

  tags = { Name = "${local.name}-engine" }
}
