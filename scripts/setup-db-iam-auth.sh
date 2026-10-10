#!/usr/bin/env bash
# One-time setup so the deployed Lambda holds no database password or Cognito client secret:
#   1. turns on IAM database authentication for the RDS instance
#   2. creates the project_hub_app login (IAM tokens only) with read/write on the app tables
#   3. stores the Cognito client secret as an SSM SecureString the Lambda reads at startup
#   4. sets the GitHub variable APP_DATABASE_URL (passwordless) used by the deploy workflow
# Reads the admin DATABASE_URL and COGNITO_APP_CLIENT_SECRET from .env. Safe to re-run.
# Run it before deploying the code that sets DATABASE_IAM_AUTH.
#
# Usage: scripts/setup-db-iam-auth.sh [stage]
set -euo pipefail
cd "$(dirname "$0")/.."

STAGE="${1:-dev}"
REGION="ca-central-1"
DB_INSTANCE="project-hub-db"
APP_USER="project_hub_app"
REPO="captainCommit/project-hub-backend-api"
SECRET_PARAM="/project-hub/${STAGE}/cognito-app-client-secret"
GH="$(command -v gh || echo "/c/Program Files/GitHub CLI/gh.exe")"

env_value() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '\r' | sed -E 's/^"(.*)"$/\1/'; }

ADMIN_URL="$(env_value DATABASE_URL | sed 's/+psycopg//')"
DB_HOST="$(echo "$ADMIN_URL" | sed -E 's#^[^@]+@([^:/]+).*#\1#')"
DB_NAME="$(echo "$ADMIN_URL" | sed -E 's#^.*/([^/?]+)(\?.*)?$#\1#')"
DB_ADMIN="$(echo "$ADMIN_URL" | sed -E 's#^[^:]+://([^:]+):.*#\1#')"
CA_BUNDLE="$(pwd)/app/certs/rds-global-bundle.pem"
command -v cygpath >/dev/null 2>&1 && CA_BUNDLE="$(cygpath -m "$CA_BUNDLE")"
# Options go before the connection string: psql on Windows stops reading options at the first plain argument.
psql_admin() { psql -v ON_ERROR_STOP=1 "$@" "${ADMIN_URL%%\?*}?sslmode=verify-full&sslrootcert=${CA_BUNDLE}"; }

echo "1/4 IAM database authentication on ${DB_INSTANCE}"
if [ "$(aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DB_INSTANCE" \
      --query 'DBInstances[0].IAMDatabaseAuthenticationEnabled' --output text)" != "True" ]; then
  aws rds modify-db-instance --region "$REGION" --db-instance-identifier "$DB_INSTANCE" \
    --enable-iam-database-authentication --apply-immediately >/dev/null
  aws rds wait db-instance-available --region "$REGION" --db-instance-identifier "$DB_INSTANCE"
fi

echo "2/4 Database login ${APP_USER} (also checks the admin connection verifies the RDS certificate)"
psql_admin -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_USER}') THEN
    CREATE ROLE ${APP_USER} LOGIN;
  END IF;
END \$\$;
GRANT rds_iam TO ${APP_USER};
GRANT CONNECT ON DATABASE ${DB_NAME} TO ${APP_USER};
GRANT USAGE ON SCHEMA public TO ${APP_USER};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ${APP_USER};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ${APP_USER};
-- Tables that future migrations create (migrations run as ${DB_ADMIN}).
ALTER DEFAULT PRIVILEGES FOR ROLE ${DB_ADMIN} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${APP_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${DB_ADMIN} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ${APP_USER};
SQL
OTHER_OWNERS="$(psql_admin -Atc "SELECT string_agg(DISTINCT tableowner, ', ') FROM pg_tables WHERE schemaname = 'public' AND tableowner <> '${DB_ADMIN}'")"
[ -n "$OTHER_OWNERS" ] && echo "   WARNING: some tables are owned by ${OTHER_OWNERS}, not ${DB_ADMIN}; check ${APP_USER} can use them."

echo "3/4 SSM SecureString ${SECRET_PARAM}"
CLIENT_SECRET="$(env_value COGNITO_APP_CLIENT_SECRET)"
[ -n "$CLIENT_SECRET" ] || { echo "COGNITO_APP_CLIENT_SECRET is empty in .env" >&2; exit 1; }
# MSYS_NO_PATHCONV stops Git Bash rewriting the /project-hub/... name into a Windows path.
MSYS_NO_PATHCONV=1 aws ssm put-parameter --region "$REGION" --name "$SECRET_PARAM" --type SecureString --overwrite \
  --value "$CLIENT_SECRET" --description "Project Hub ${STAGE} Cognito app client secret" >/dev/null

echo "4/4 GitHub variable APP_DATABASE_URL (${STAGE} environment)"
APP_URL="postgresql+psycopg://${APP_USER}@${DB_HOST}:5432/${DB_NAME}"
"$GH" variable set APP_DATABASE_URL --repo "$REPO" --env "$STAGE" --body "$APP_URL"

echo
echo "Checking an IAM-token sign-in as ${APP_USER} (needs rds-db:connect for your own AWS user; skip if denied)..."
if TOKEN="$(aws rds generate-db-auth-token --region "$REGION" --hostname "$DB_HOST" --port 5432 --username "$APP_USER")" &&
   PGPASSWORD="$TOKEN" psql \
     -Atc "SELECT 'ok: signed in as ' || current_user || ', ' || count(*) || ' tables readable' FROM information_schema.tables WHERE table_schema = 'public' AND has_table_privilege(table_name::regclass, 'SELECT')" \
     "host=${DB_HOST} dbname=${DB_NAME} user=${APP_USER} sslmode=verify-full sslrootcert=${CA_BUNDLE}"; then
  :
else
  echo "   Could not sign in with a token from your AWS user; the Lambda role has its own permission, so this may be fine."
fi

echo
echo "Done. Deploy the API (push to dev), then remove the old secret it no longer uses:"
echo "  gh secret delete COGNITO_APP_CLIENT_SECRET --repo ${REPO} --env ${STAGE}"
