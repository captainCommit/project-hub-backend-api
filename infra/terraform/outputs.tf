# scripts/bootstrap-account.sh reads these.

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "region" {
  value = var.region
}

output "stage" {
  value = var.stage
}

output "github_owner" {
  value = var.github_owner
}

output "github_api_repo" {
  value = var.github_api_repo
}

output "github_ui_repo" {
  value = var.github_ui_repo
}

output "github_api_sub" {
  description = "OIDC subject the API deploy role trusts."
  value       = "${local.api_sub_prefix}:environment:${var.stage}"
}

output "github_ui_sub" {
  description = "OIDC subject the UI deploy role trusts."
  value       = "${local.ui_sub_prefix}:environment:${var.stage}"
}

output "api_deploy_role_arn" {
  value = aws_iam_role.api_deploy.arn
}

output "ui_deploy_role_arn" {
  value = aws_iam_role.ui_deploy.arn
}

output "db_instance_identifier" {
  value = aws_db_instance.main.identifier
}

output "db_host" {
  value = local.db_host
}

output "db_name" {
  value = var.db_name
}

output "db_admin_username" {
  value = var.db_admin_username
}

output "db_app_username" {
  value = var.db_app_username
}

output "app_database_url" {
  description = "GitHub variable APP_DATABASE_URL (passwordless)."
  value       = local.app_database_url
}

output "database_admin_url" {
  description = "GitHub secret DATABASE_URL."
  value       = local.database_admin_url
  sensitive   = true
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_domain_url" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}

output "cognito_callback_urls" {
  value = aws_cognito_user_pool_client.web.callback_urls
}
