# GitHub Actions deploys with OIDC (no stored AWS keys). Each role can only be assumed by its repo's jobs
# running in the GitHub environment named after the stage. Mirrors scripts/setup-github-deploy-roles.sh.

locals {
  github_oidc_url = "token.actions.githubusercontent.com"
  api_stack       = "project-hub-api-${var.stage}"

  api_sub_prefix = coalesce(var.github_api_sub_prefix, "repo:${var.github_owner}/${var.github_api_repo}")
  ui_sub_prefix  = coalesce(var.github_ui_sub_prefix, "repo:${var.github_owner}/${var.github_ui_repo}")

  github_oidc_provider_arn = (
    var.create_github_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : data.aws_iam_openid_connect_provider.github[0].arn
  )
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://${local.github_oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://${local.github_oidc_url}"
}

data "aws_iam_policy_document" "github_trust" {
  for_each = {
    api = local.api_sub_prefix
    ui  = local.ui_sub_prefix
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:sub"
      values   = ["${each.value}:environment:${var.stage}"]
    }
  }
}

# --- API repo: `serverless deploy` creates and updates the whole app stack -----------------------------------

resource "aws_iam_role" "api_deploy" {
  name               = "project-hub-api-github-deploy"
  description        = "GitHub Actions deploys for ${var.github_owner}/${var.github_api_repo} (${var.stage})"
  assume_role_policy = data.aws_iam_policy_document.github_trust["api"].json
}

resource "aws_iam_role_policy_attachment" "api_deploy_power_user" {
  role       = aws_iam_role.api_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

# PowerUserAccess has no IAM; the stack creates its Lambda roles, all named project-hub-api-*.
data "aws_iam_policy_document" "api_deploy_iam" {
  statement {
    sid = "ManageStackLambdaRoles"
    actions = [
      "iam:GetRole", "iam:CreateRole", "iam:DeleteRole", "iam:UpdateRole", "iam:TagRole", "iam:UntagRole",
      "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PassRole",
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/project-hub-api-*"]
  }
}

resource "aws_iam_role_policy" "api_deploy_iam" {
  name   = "stack-lambda-roles"
  role   = aws_iam_role.api_deploy.id
  policy = data.aws_iam_policy_document.api_deploy_iam.json
}

# --- UI repo: upload the build to the stack's UI bucket and invalidate the distribution -----------------------

resource "aws_iam_role" "ui_deploy" {
  name               = "project-hub-ui-github-deploy"
  description        = "GitHub Actions deploys for ${var.github_owner}/${var.github_ui_repo} (${var.stage})"
  assume_role_policy = data.aws_iam_policy_document.github_trust["ui"].json
}

data "aws_iam_policy_document" "ui_deploy" {
  statement {
    sid       = "ReadStackOutputs"
    actions   = ["cloudformation:DescribeStacks"]
    resources = ["arn:aws:cloudformation:${var.region}:${data.aws_caller_identity.current.account_id}:stack/${local.api_stack}/*"]
  }
  statement {
    sid     = "SyncUiBucket"
    actions = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "arn:aws:s3:::${local.api_stack}-uibucket-*",
      "arn:aws:s3:::${local.api_stack}-uibucket-*/*",
    ]
  }
  statement {
    sid       = "InvalidateDistribution"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = ["arn:aws:cloudfront::${data.aws_caller_identity.current.account_id}:distribution/*"]
  }
}

resource "aws_iam_role_policy" "ui_deploy" {
  name   = "ui-deploy"
  role   = aws_iam_role.ui_deploy.id
  policy = data.aws_iam_policy_document.ui_deploy.json
}
