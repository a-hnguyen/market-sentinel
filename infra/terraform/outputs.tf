output "overlay_bucket" {
  description = "Private bucket for the strategy overlay + archives."
  value       = aws_s3_bucket.overlay.id
}

output "ssm_param_prefix" {
  description = "Path prefix under which to set real secret values."
  value       = local.ssm_prefix
}

output "engine_role_arn" {
  description = "IAM role the engine box assumes."
  value       = aws_iam_role.engine.arn
}

output "log_group" {
  description = "CloudWatch log group for engine output."
  value       = aws_cloudwatch_log_group.engine.name
}

output "vpc_id" {
  description = "Default VPC the engine runs in."
  value       = data.aws_vpc.default.id
}

output "instance_id" {
  description = "EC2 instance running the engine."
  value       = aws_instance.engine.id
}

output "public_ip" {
  description = "Public IP (outbound egress only; nothing listens inbound)."
  value       = aws_instance.engine.public_ip
}

output "sns_topic_arn" {
  description = "SNS topic for ops alerts (box status check + engine crash-loop)."
  value       = aws_sns_topic.ops.arn
}

output "prescreen_trigger_lambda" {
  description = "Lambda that EventBridge invokes to kick off the pre-screen."
  value       = aws_lambda_function.prescreen_trigger.function_name
}

output "prescreen_schedule" {
  description = "EventBridge cron (UTC) for the pre-screen trigger."
  value       = aws_cloudwatch_event_rule.prescreen.schedule_expression
}

output "ci_deploy_role_arn" {
  description = "Set as the GitHub repo variable AWS_DEPLOY_ROLE_ARN (the CI workflow assumes this via OIDC)."
  value       = aws_iam_role.ci_deploy.arn
}

output "console_overview" {
  description = "Single-pane console view of every Project-tagged resource."
  value       = "https://${var.region}.console.aws.amazon.com/resource-groups/group/${aws_resourcegroups_group.all.name}?region=${var.region}"
}

output "ssm_session_command" {
  description = "Open a shell on the box (no SSH). Requires the AWS CLI + Session Manager plugin."
  value       = "aws ssm start-session --target ${aws_instance.engine.id} --region ${var.region}"
}
