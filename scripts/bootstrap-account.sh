#!/usr/bin/env bash
# Wires a freshly applied infra/terraform stack up to the app. Safe to re-run; each step only overwrites.
#
#   ssm     SSM SecureStrings for third-party secrets (Stripe, Slack, Microsoft, platform AI keys)
#   db      the project_hub_app login (IAM tokens only) with read/write on the app tables
#   github  the stage environment, variables and the DATABASE_URL secret in both repos
#   users   (only when asked for) Cognito invites for the people in a database moved from another account
#
# Inputs: `terraform output` from infra/terraform, and infra/bootstrap.env (copy bootstrap.env.example).
# Needs: aws (signed in to the target account), terraform, gh (signed in), psql for the db and users steps.
#
# Usage: scripts/bootstrap-account.sh [--env-file FILE] [--dry-run] [ssm] [db] [github] [users]
#        (no steps = ssm db github)
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="infra/bootstrap.env"
TF_DIR="infra/terraform"
DRY_RUN=false
STEPS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    ssm|db|github|users) STEPS+=("$1"); shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ ${#STEPS[@]} -gt 0 ] || STEPS=(ssm db github)

[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE (copy infra/bootstrap.env.example)." >&2; exit 1; }
GH="$(command -v gh || echo "/c/Program Files/GitHub CLI/gh.exe")"
# Git Bash on Windows: stop /project-hub/... arguments being rewritten into Windows paths.
export MSYS_NO_PATHCONV=1

env_value() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -E 's/^"(.*)"$/\1/' || true; }
tf() { terraform -chdir="$TF_DIR" output -raw "$1"; }
run() { if $DRY_RUN; then echo "   (dry run) $*" | sed -E 's/(--value|--body) [^ ]+/\1 ****/'; else "$@"; fi; }

REGION="$(tf region)"
STAGE="$(tf stage)"
ACCOUNT_ID="$(tf account_id)"
CURRENT_ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
if [ "$CURRENT_ACCOUNT" != "$ACCOUNT_ID" ]; then
  echo "The AWS CLI is signed in to ${CURRENT_ACCOUNT}, but the Terraform state is for ${ACCOUNT_ID}." >&2
  exit 1
fi
echo "Account ${ACCOUNT_ID}, region ${REGION}, stage ${STAGE}"

step_ssm() {
  echo
  echo "== SSM SecureStrings under /project-hub/${STAGE}/"
  local entry key name value
  for entry in \
    STRIPE_SECRET_KEY:stripe-secret-key \
    STRIPE_WEBHOOK_SECRET:stripe-webhook-secret \
    SLACK_CLIENT_SECRET:integrations/slack-client-secret \
    SLACK_SIGNING_SECRET:integrations/slack-signing-secret \
    MICROSOFT_CLIENT_SECRET:integrations/microsoft-client-secret \
    AI_OPENAI_API_KEY:ai/openai-api-key \
    AI_ANTHROPIC_API_KEY:ai/anthropic-api-key \
    AI_GEMINI_API_KEY:ai/gemini-api-key; do
    key="${entry%%:*}"
    name="/project-hub/${STAGE}/${entry#*:}"
    value="$(env_value "$key")"
    if [ -z "$value" ]; then
      echo "   skip ${name} (${key} is empty)"
      continue
    fi
    run aws ssm put-parameter --region "$REGION" --name "$name" --type SecureString --overwrite \
      --description "Project Hub ${STAGE} ${entry#*:}" --value "$value" >/dev/null
    echo "   set  ${name}"
  done
  echo "   (Terraform owns cognito-app-client-secret, database-admin-url and integrations/state-secret.)"
}

step_db() {
  echo
  echo "== Database login $(tf db_app_username)"
  local admin_url app_user db_name db_admin ca_bundle
  admin_url="$(tf database_admin_url | sed 's/+psycopg//')"
  app_user="$(tf db_app_username)"
  db_name="$(tf db_name)"
  db_admin="$(tf db_admin_username)"
  ca_bundle="$(pwd)/app/certs/rds-global-bundle.pem"
  command -v cygpath >/dev/null 2>&1 && ca_bundle="$(cygpath -m "$ca_bundle")"
  if $DRY_RUN; then echo "   (dry run) would create ${app_user} and grant it read/write on ${db_name}"; return; fi
  command -v psql >/dev/null 2>&1 || { echo "psql is not on PATH" >&2; exit 1; }
  # Options go before the connection string: psql on Windows stops reading options at the first plain argument.
  psql -v ON_ERROR_STOP=1 -q "${admin_url%%\?*}?sslmode=verify-full&sslrootcert=${ca_bundle}" <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${app_user}') THEN
    CREATE ROLE ${app_user} LOGIN;
  END IF;
END \$\$;
GRANT rds_iam TO ${app_user};
GRANT CONNECT ON DATABASE ${db_name} TO ${app_user};
GRANT USAGE ON SCHEMA public TO ${app_user};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ${app_user};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ${app_user};
-- Tables that later migrations create (migrations run as ${db_admin}).
ALTER DEFAULT PRIVILEGES FOR ROLE ${db_admin} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${app_user};
ALTER DEFAULT PRIVILEGES FOR ROLE ${db_admin} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ${app_user};
SQL
  echo "   ${app_user} can sign in with IAM tokens"
}

gh_var() { # repo name value
  if [ -z "$3" ]; then echo "   skip ${2} (empty)"; return; fi
  run "$GH" variable set "$2" --repo "$1" --env "$STAGE" --body "$3" >/dev/null
  echo "   set  ${2}"
}

# Warns when the deploy role trusts a different OIDC subject than the repo's tokens will carry.
check_oidc_sub() { # repo expected_sub
  local prefix
  prefix="$("$GH" api "repos/$1/actions/oidc/customization/sub" --jq '.sub_claim_prefix // empty' 2>/dev/null | tr -d '[:space:]' || true)"
  prefix="${prefix:-repo:$1}"
  if [ "${prefix}:environment:${STAGE}" != "$2" ]; then
    echo "   WARNING: $1 sends the OIDC subject '${prefix}:environment:${STAGE}', but its role trusts '$2'."
    echo "            Set github_*_sub_prefix = \"${prefix}\" in terraform.tfvars and apply again."
  fi
}

step_github() {
  local owner api_repo ui_repo app_url key
  owner="$(tf github_owner)"
  api_repo="${owner}/$(tf github_api_repo)"
  ui_repo="${owner}/$(tf github_ui_repo)"

  app_url="$(env_value APP_URL)"
  if [ -z "$app_url" ]; then
    app_url="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "project-hub-api-${STAGE}" \
      --query "Stacks[0].Outputs[?OutputKey=='AppUrl'].OutputValue" --output text 2>/dev/null || true)"
    [ "$app_url" = "None" ] && app_url=""
  fi

  for repo in "$api_repo" "$ui_repo"; do
    echo
    echo "== ${repo} (environment ${STAGE})"
    run "$GH" api -X PUT "repos/${repo}/environments/${STAGE}" >/dev/null
  done

  echo
  echo "== ${api_repo} variables"
  check_oidc_sub "$api_repo" "$(tf github_api_sub)"
  gh_var "$api_repo" AWS_DEPLOY_ROLE_ARN "$(tf api_deploy_role_arn)"
  gh_var "$api_repo" APP_DATABASE_URL "$(tf app_database_url)"
  gh_var "$api_repo" COGNITO_USER_POOL_ID "$(tf cognito_user_pool_id)"
  gh_var "$api_repo" COGNITO_APP_CLIENT_ID "$(tf cognito_app_client_id)"
  gh_var "$api_repo" COGNITO_DOMAIN "$(tf cognito_domain_url)"
  gh_var "$api_repo" APP_URL "$app_url"
  for key in ALARM_EMAIL EMAIL_FROM COGNITO_ADDITIONAL_CLIENT_IDS STRIPE_FULL_SEAT_PRICE_ID \
             STRIPE_VIEWER_SEAT_PRICE_ID STRIPE_AI_USAGE_PRICE_ID SLACK_CLIENT_ID MICROSOFT_CLIENT_ID; do
    gh_var "$api_repo" "$key" "$(env_value "$key")"
  done
  # Through stdin, so the password never appears in a process listing.
  if $DRY_RUN; then
    echo "   (dry run) would set secret DATABASE_URL"
  else
    tf database_admin_url | "$GH" secret set DATABASE_URL --repo "$api_repo" --env "$STAGE" >/dev/null
    echo "   set  secret DATABASE_URL"
  fi

  echo
  echo "== ${ui_repo} variables"
  check_oidc_sub "$ui_repo" "$(tf github_ui_sub)"
  gh_var "$ui_repo" AWS_DEPLOY_ROLE_ARN "$(tf ui_deploy_role_arn)"
  gh_var "$ui_repo" VITE_COGNITO_CLIENT_ID "$(tf cognito_app_client_id)"
  gh_var "$ui_repo" VITE_COGNITO_DOMAIN "$(tf cognito_domain_url)"

  if [ -z "$app_url" ]; then
    echo
    echo "APP_URL is not set yet: deploy the API, then run '$0 github' again and redeploy the API."
  elif ! terraform -chdir="$TF_DIR" output -json cognito_callback_urls | grep -q "${app_url%/}/auth/callback"; then
    echo
    echo "Sign-in from ${app_url} isn't allowed yet: add it to app_urls in terraform.tfvars and apply."
  fi
}

# Only after moving the data: existing people have the old pool's sub, so the app won't invite them again.
# Creating them here (email verified) sends Cognito's invite; on first sign-in the API links them by email.
step_users() {
  echo
  echo "== Cognito invites for active users in the database"
  local admin_url pool email count=0
  admin_url="$(tf database_admin_url | sed 's/+psycopg//')"
  pool="$(tf cognito_user_pool_id)"
  while IFS= read -r email; do
    email="$(echo "$email" | tr -d '\r')"
    [ -n "$email" ] || continue
    if aws cognito-idp admin-get-user --region "$REGION" --user-pool-id "$pool" --username "$email" >/dev/null 2>&1; then
      echo "   exists ${email}"
      continue
    fi
    run aws cognito-idp admin-create-user --region "$REGION" --user-pool-id "$pool" --username "$email" \
      --user-attributes "Name=email,Value=${email}" Name=email_verified,Value=true \
      --desired-delivery-mediums EMAIL >/dev/null
    echo "   invited ${email}"
    count=$((count + 1))
  done < <(psql -At -c "SELECT email FROM users WHERE is_active AND cognito_sub IS NOT NULL ORDER BY email" \
             "${admin_url%%\?*}?sslmode=require")
  echo "   ${count} invited (Cognito's built-in sender allows about 50 emails a day)"
}

for step in "${STEPS[@]}"; do "step_${step}"; done
echo
echo "Done."
