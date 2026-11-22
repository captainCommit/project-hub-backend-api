# Signs the short-lived OAuth state for Slack / Microsoft connects. Nothing long-lived depends on it, so a new
# account simply gets a new value.
resource "random_password" "integration_state_secret" {
  length  = 64
  special = false
}

resource "aws_ssm_parameter" "integration_state_secret" {
  name        = "/project-hub/${var.stage}/integrations/state-secret"
  description = "Project Hub ${var.stage} OAuth state signing secret"
  type        = "SecureString"
  value       = random_password.integration_state_secret.result
}

# EMAIL_FROM must be a verified identity. Addresses get a verification email; domains need the DKIM records
# from the console. Sending to unverified recipients also needs SES production access (a support request).
resource "aws_sesv2_email_identity" "sender" {
  for_each       = toset(var.ses_email_identities)
  email_identity = each.value
}

# New accounts are limited to 10 concurrent Lambda executions, shared by the API and every worker.
resource "aws_servicequotas_service_quota" "lambda_concurrency" {
  count        = var.lambda_concurrency_quota == null ? 0 : 1
  service_code = "lambda"
  quota_code   = "L-B99A9384"
  value        = var.lambda_concurrency_quota
}
