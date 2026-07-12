# GitHub Actions → AWS via OIDC. No long-lived AWS keys stored in GitHub: the
# workflow presents a short-lived OIDC token, AWS trades it for temporary creds
# scoped to this role. The trust policy pins the token to THIS repo's main
# branch, so only a push to main (not a fork, not a PR) can assume the role.

# AWS's well-known OIDC provider for GitHub Actions. One per account; if the
# account already has it, set create_github_oidc_provider=false and this is
# skipped (the role below then references the existing provider's ARN).
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub's OIDC root CA thumbprint. AWS also verifies the provider out-of-band,
  # so this is belt-and-suspenders, but the argument is required.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
  tags            = { Name = "${local.name}-github-oidc" }
}

locals {
  github_oidc_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "ci_deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only this repo's target branch can assume the role — not PRs, not forks.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/${var.repo_branch}"]
    }
  }
}

resource "aws_iam_role" "ci_deploy" {
  name_prefix        = "${local.name}-ci-deploy-"
  assume_role_policy = data.aws_iam_policy_document.ci_deploy_assume.json
  tags               = { Name = "${local.name}-ci-deploy" }
}

# Least-privilege: the CI role can only ask SSM to run the shell doc on the
# tagged box and read back the result. No ssm:* wildcard, no ec2:* — the same
# tag-scoped SendCommand the pre-screen Lambda uses.
data "aws_iam_policy_document" "ci_deploy" {
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

  # Poll the command result. These read-only actions have no resource-level
  # scoping in SSM, so they target "*".
  statement {
    sid       = "ReadCommandResult"
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ci_deploy" {
  name   = "${local.name}-ci-deploy"
  role   = aws_iam_role.ci_deploy.id
  policy = data.aws_iam_policy_document.ci_deploy.json
}
