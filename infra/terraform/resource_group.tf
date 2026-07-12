# A single named console view of the whole deploy. Every resource inherits
# Project=<project> via the provider's default_tags (main.tf), so this tag query
# collects the EC2 box, Lambda, EventBridge rule, SNS topic, CloudWatch
# alarm/log groups, S3 bucket, etc. into one Resource Groups page — the
# closest thing AWS has to a single "overview" screen for this stack.
resource "aws_resourcegroups_group" "all" {
  name        = "${local.name}-all"
  description = "Every ${local.name} resource grouped by the Project tag."

  resource_query {
    query = jsonencode({
      ResourceTypeFilters = ["AWS::AllSupported"]
      TagFilters = [
        {
          Key    = "Project"
          Values = [var.project]
        },
      ]
    })
  }

  tags = { Name = "${local.name}-all" }
}
