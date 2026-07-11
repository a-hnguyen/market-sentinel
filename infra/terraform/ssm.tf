# Secrets/config as SSM Parameter Store SecureStrings. Terraform creates the
# parameter *slots* with placeholder values and ignores drift on `value`, so the
# real secrets are set out-of-band (CLI/console) and never live in git or state
# as plaintext you typed. The box reads them at boot via its instance role.
#
# Set real values after apply, e.g.:
#   aws ssm put-parameter --name /market-sentinel/alpaca_api_key \
#     --type SecureString --value 'PK...' --overwrite
locals {
  ssm_prefix = "/${local.name}"

  ssm_params = {
    alpaca_api_key    = "SET_ME"
    alpaca_secret_key = "SET_ME"
    ntfy_topic        = "SET_ME"
  }
}

resource "aws_ssm_parameter" "config" {
  for_each = local.ssm_params

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "SecureString"
  value = each.value

  lifecycle {
    # Real values are managed out-of-band; don't let Terraform revert them.
    ignore_changes = [value]
  }

  tags = { Name = "${local.name}-${each.key}" }
}
