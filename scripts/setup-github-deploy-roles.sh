#!/usr/bin/env bash
# One-time setup: let GitHub Actions deploy Project Hub dev to AWS without stored access keys.
# Creates the GitHub OIDC identity provider and one IAM role per repo, each assumable only by
# that repo's jobs running in the GitHub "dev" environment. Safe to re-run.
#
# Usage: scripts/setup-github-deploy-roles.sh [github-owner]
set -euo pipefail

OWNER="${1:-captainCommit}"
API_REPO="project-hub-backend-api"
UI_REPO="project-hub-ui"
STACK="project-hub-api-dev"
REGION="ca-central-1"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
TMP="$(mktemp -d)"
# Git Bash on Windows: the native aws CLI can't resolve /tmp, so hand it a C:/... path instead.
if command -v cygpath >/dev/null 2>&1; then TMP="$(cygpath -m "$TMP")"; fi
trap 'rm -rf "$TMP"' EXIT

if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com >/dev/null
  echo "Created GitHub OIDC provider"
fi

GH="$(command -v gh || echo "/c/Program Files/GitHub CLI/gh.exe")"

# The token's subject starts with the repo's own prefix: "repo:owner/name" by default, but repos on
# GitHub's immutable subject format use "repo:owner@<id>/name@<id>". Ask GitHub which one it is.
sub_prefix() {
  "$GH" api "repos/${OWNER}/$1/actions/oidc/customization/sub" --jq '.sub_claim_prefix // empty' 2>/dev/null || true
}

trust_policy() {
  local prefix
  prefix="$(sub_prefix "$1" | tr -d "[:space:]")"
  prefix="${prefix:-repo:${OWNER}/$1}"
  cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "${PROVIDER_ARN}" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "${prefix}:environment:dev"
      }
    }
  }]
}
JSON
}

ensure_role() {
  local role="$1" repo="$2"
  trust_policy "$repo" > "$TMP/trust.json"
  if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$role" --policy-document "file://$TMP/trust.json"
  else
    aws iam create-role --role-name "$role" --assume-role-policy-document "file://$TMP/trust.json" \
      --description "GitHub Actions deploys for ${OWNER}/${repo} (dev)" >/dev/null
  fi
}

# API repo: serverless deploy creates/updates the CloudFormation stack (Lambda, HTTP API, S3, CloudFront).
API_ROLE="project-hub-api-github-deploy"
ensure_role "$API_ROLE" "$API_REPO"
aws iam attach-role-policy --role-name "$API_ROLE" --policy-arn arn:aws:iam::aws:policy/PowerUserAccess
cat > "$TMP/api-iam.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "ManageStackLambdaRoles",
    "Effect": "Allow",
    "Action": [
      "iam:GetRole", "iam:CreateRole", "iam:DeleteRole", "iam:UpdateRole", "iam:TagRole", "iam:UntagRole",
      "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PassRole"
    ],
    "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/project-hub-api-*"
  }]
}
JSON
aws iam put-role-policy --role-name "$API_ROLE" --policy-name stack-lambda-roles --policy-document "file://$TMP/api-iam.json"

# UI repo: upload the build to the stack's UI bucket and invalidate the distribution.
UI_ROLE="project-hub-ui-github-deploy"
ensure_role "$UI_ROLE" "$UI_REPO"
cat > "$TMP/ui.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadStackOutputs",
      "Effect": "Allow",
      "Action": "cloudformation:DescribeStacks",
      "Resource": "arn:aws:cloudformation:${REGION}:${ACCOUNT_ID}:stack/${STACK}/*"
    },
    {
      "Sid": "SyncUiBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::${STACK}-uibucket-*", "arn:aws:s3:::${STACK}-uibucket-*/*"]
    },
    {
      "Sid": "InvalidateDistribution",
      "Effect": "Allow",
      "Action": "cloudfront:CreateInvalidation",
      "Resource": "arn:aws:cloudfront::${ACCOUNT_ID}:distribution/*"
    }
  ]
}
JSON
aws iam put-role-policy --role-name "$UI_ROLE" --policy-name ui-deploy --policy-document "file://$TMP/ui.json"

echo
echo "Set the GitHub variable AWS_DEPLOY_ROLE_ARN (environment 'dev') in each repo:"
echo "  ${API_REPO}: arn:aws:iam::${ACCOUNT_ID}:role/${API_ROLE}"
echo "  ${UI_REPO}:  arn:aws:iam::${ACCOUNT_ID}:role/${UI_ROLE}"
