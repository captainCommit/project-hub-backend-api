# Invite-only email sign-in with the managed login (v2) pages. The API exchanges the code with the client
# secret and sets the session cookies (see README "Browser sessions").

resource "aws_cognito_user_pool" "main" {
  name                     = var.cognito_user_pool_name
  user_pool_tier           = "ESSENTIALS"
  deletion_protection      = var.cognito_deletion_protection ? "ACTIVE" : "INACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OFF"

  username_configuration {
    case_sensitive = false
  }

  # People are added from the app (AdminCreateUser emails the invite).
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 8
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  sign_in_policy {
    allowed_first_auth_factors = ["PASSWORD"]
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain                = var.cognito_domain_prefix
  user_pool_id          = aws_cognito_user_pool.main.id
  managed_login_version = 2
}

locals {
  app_origins = [for url in concat(var.local_dev_urls, var.app_urls) : trimsuffix(url, "/")]
}

resource "aws_cognito_user_pool_client" "web" {
  name            = "project-hub-web"
  user_pool_id    = aws_cognito_user_pool.main.id
  generate_secret = true

  explicit_auth_flows          = ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
  supported_identity_providers = ["COGNITO"]

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["email", "openid", "profile"]
  callback_urls                        = [for origin in local.app_origins : "${origin}/auth/callback"]
  logout_urls                          = [for origin in local.app_origins : "${origin}/"]

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 5
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  auth_session_validity         = 3
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
}

# Managed login v2 shows nothing until the client has a branding style; this is the Cognito default style.
resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.main.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true
}

# Read by the API at startup (COGNITO_APP_CLIENT_SECRET_PARAM in serverless.yml).
resource "aws_ssm_parameter" "cognito_client_secret" {
  name        = "/project-hub/${var.stage}/cognito-app-client-secret"
  description = "Project Hub ${var.stage} Cognito app client secret"
  type        = "SecureString"
  value       = aws_cognito_user_pool_client.web.client_secret
}
