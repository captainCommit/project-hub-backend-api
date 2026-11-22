variable "region" {
  description = "Region for everything. The deploy workflows and serverless.yml assume ca-central-1."
  type        = string
  default     = "ca-central-1"
}

variable "stage" {
  description = "Stage name; must match `serverless deploy --stage` and the GitHub environment name."
  type        = string
  default     = "dev"
}

# --- GitHub ---------------------------------------------------------------------------------------------------

variable "github_owner" {
  description = "GitHub user or organisation that owns both repos."
  type        = string
  default     = "captainCommit"
}

variable "github_api_repo" {
  type    = string
  default = "project-hub-backend-api"
}

variable "github_ui_repo" {
  type    = string
  default = "project-hub-ui"
}

variable "github_api_sub_prefix" {
  description = <<-EOT
    OIDC subject prefix of the API repo. Null means the classic "repo:<owner>/<repo>". Repos on GitHub's immutable
    subject format use "repo:<owner>@<id>/<repo>@<id>"; get it with
    gh api repos/<owner>/<repo>/actions/oidc/customization/sub --jq .sub_claim_prefix
  EOT
  type        = string
  default     = null
}

variable "github_ui_sub_prefix" {
  description = "Same as github_api_sub_prefix, for the UI repo (which uses the immutable format today)."
  type        = string
  default     = null
}

variable "create_github_oidc_provider" {
  description = "False if the account already has the token.actions.githubusercontent.com provider."
  type        = bool
  default     = true
}

# --- Database -------------------------------------------------------------------------------------------------

variable "db_instance_identifier" {
  description = "Also used by the serverless DB alarms (DB_INSTANCE_ID, default project-hub-db)."
  type        = string
  default     = "project-hub-db"
}

variable "db_engine_version" {
  description = "Major version only, so automatic minor upgrades don't show up as drift."
  type        = string
  default     = "18"
}

variable "db_parameter_group_name" {
  type    = string
  default = "default.postgres18"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_name" {
  type    = string
  default = "project_hub"
}

variable "db_admin_username" {
  description = "Master user; migrations run as this user (the GitHub secret DATABASE_URL)."
  type        = string
  default     = "project_admin"
}

variable "db_app_username" {
  description = "IAM-auth login the Lambdas use. serverless.yml grants rds-db:connect for project_hub_app."
  type        = string
  default     = "project_hub_app"
}

variable "db_allowed_cidrs" {
  description = "Who can reach 5432. The Lambdas run outside a VPC, so today this is the whole internet (SSL + auth)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "db_backup_retention_days" {
  type    = number
  default = 1
}

variable "db_deletion_protection" {
  type    = bool
  default = false
}

variable "db_snapshot_identifier" {
  description = <<-EOT
    Restore the new instance from this snapshot (ARN or name in this account) instead of starting empty. To bring
    data from another account, share a snapshot encrypted with a customer-managed KMS key and copy it here first.
  EOT
  type        = string
  default     = null
}

# --- Cognito --------------------------------------------------------------------------------------------------

variable "cognito_user_pool_name" {
  type    = string
  default = "project-hub"
}

variable "cognito_domain_prefix" {
  description = "Hosted sign-in prefix (<prefix>.auth.<region>.amazoncognito.com). Unique per region across AWS, so it must differ from the old account's while that pool exists."
  type        = string
  default     = "projecthub-auth"
}

variable "cognito_deletion_protection" {
  type    = bool
  default = true
}

variable "app_urls" {
  description = <<-EOT
    Deployed app origins allowed as sign-in callbacks, e.g. ["https://dxxxx.cloudfront.net"]. Leave empty for the
    first apply; add the AppUrl output of the serverless stack after the first API deploy and apply again.
  EOT
  type        = list(string)
  default     = []
}

variable "local_dev_urls" {
  description = "Vite dev server origins allowed as callbacks."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

# --- Other ----------------------------------------------------------------------------------------------------

variable "ses_email_identities" {
  description = "Sender addresses or domains to verify in SES (EMAIL_FROM must use one). Each address gets a verification email."
  type        = list(string)
  default     = []
}

variable "lambda_concurrency_quota" {
  description = "Request this account-level Lambda concurrency (new accounts get 10, shared by the API and all workers). Null skips the request."
  type        = number
  default     = null
}
