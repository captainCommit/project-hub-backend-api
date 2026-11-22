# Rebuilding Project Hub in a new AWS account

Infrastructure is split in two:

| Layer | Tool | What |
| --- | --- | --- |
| Foundation | `infra/terraform` (this folder) | RDS Postgres and its security group, Cognito pool, domain, client and branding, GitHub OIDC provider and deploy roles, SES sender identities, the Lambda concurrency quota request, and the SSM parameters Terraform can produce itself. |
| App stack | `serverless.yml` (deployed by GitHub Actions) | Lambdas and schedules, HTTP API, CloudFront, UI and attachment buckets, KMS keys, Secrets Manager secrets (origin verify, email token), SES configuration set, SNS topics, alarms. |

The app stack is left to Serverless because CI deploys it on every push to `dev`. Owning it in Terraform as
well would give two tools the same resources.

Secrets that come from outside AWS (Stripe, Slack, Microsoft, AI provider keys) are not in Terraform, so they
never reach the state. `scripts/bootstrap-account.sh` writes them to SSM and sets the GitHub variables.

## Prerequisites

- AWS CLI signed in to the **new** account with admin rights (`aws sts get-caller-identity`)
- Terraform 1.6 or later, `gh` signed in with admin rights on both repos, and `psql`
- Git Bash on Windows

## Steps

1. **Configure.**

       cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
       cp infra/bootstrap.env.example infra/bootstrap.env

   In `terraform.tfvars`, choose a `cognito_domain_prefix` that isn't already taken, and check each repo's OIDC
   subject prefix (the example file has the command). Fill in `bootstrap.env`.

2. **Foundation.**

       cd infra/terraform && terraform init && terraform apply

   RDS takes about 10 minutes. If you listed SES identities, confirm the verification emails AWS sends.

3. **Move the data** (optional; see below). Skip this for an empty start.

4. **Secrets, DB login and GitHub.** From `project-hub-api/`:

       scripts/bootstrap-account.sh --dry-run   # shows what it would set
       scripts/bootstrap-account.sh

   On a first run it says `APP_URL` isn't set yet. That is expected.

5. **Deploy the API.** Push to `dev`, or run *Deploy Dev API* from the Actions tab. Migrations run first (to
   head on an empty database), then `serverless deploy` creates the `project-hub-api-dev` stack.

6. **Connect the app URL.** Take the stack's `AppUrl` output (the workflow prints it):
   - add it to `app_urls` in `terraform.tfvars` and run `terraform apply` again (Cognito callback and sign-out URLs)
   - run `scripts/bootstrap-account.sh github` again to set `APP_URL`
   - redeploy the API (it reads `APP_URL` at deploy time), then deploy the UI (push to `dev` in project-hub-ui)

7. **Third parties.** Point each one at the new URL:
   - Stripe: add a webhook endpoint at `<APP_URL>/api/v1/billing/stripe-webhook`, put its signing secret in
     `STRIPE_WEBHOOK_SECRET`, and run `scripts/bootstrap-account.sh ssm`
   - Slack and Entra app redirect, command and event URLs (API README, "Integrations")

8. **Finish up.**
   - Confirm the SNS alarm subscription email.
   - Request SES production access (support case), or email only reaches verified addresses.
   - On an empty database, sign in once and run `scripts/grant_platform_admin.py <email>` with `DATABASE_URL`
     set to the admin URL (`terraform output -raw database_admin_url`).

## Moving the data

**Database.** The quickest route for a database this size is a dump and restore. Do it after step 2 and before
step 4. The `db` step grants on the restored tables, or re-run `scripts/bootstrap-account.sh db` afterwards.

    pg_dump -Fc --no-owner --no-privileges "<old admin url>" -f project_hub.dump
    pg_restore --no-owner --no-privileges -d "$(terraform -chdir=infra/terraform output -raw database_admin_url | sed 's/+psycopg//')" project_hub.dump

`alembic_version` comes along, so the first deploy only applies newer migrations. A snapshot also works. Copy
it in the old account with a customer-managed KMS key (snapshots on the default `aws/rds` key can't be
shared), share the snapshot and the key, copy it in the new account, and set `db_snapshot_identifier` before
the first `terraform apply`.

**People.** Cognito users can't be exported with their passwords. After restoring, run
`scripts/bootstrap-account.sh users`. It invites every active user who had signed in before, and on first
sign-in the API links them to their existing row by verified email. Cognito's built-in sender allows about 50
emails a day.

**Attachments.** After step 5, copy the old attachments bucket into the new `AttachmentsBucketName` output
with the same keys. For example, `aws s3 sync s3://<old> ./attachments` signed in to the old account, then
`aws s3 sync ./attachments s3://<new>` signed in to the new one.

## Notes

- Region and stack name are also hard-coded in both repos' `deploy-dev.yml` (`ca-central-1`,
  `project-hub-api-dev`). Change them there too if you change `region` or `stage`.
- `scripts/add_cognito_app_url.py`, `setup-github-deploy-roles.sh` and `setup-db-iam-auth.sh` set up the
  original account by hand. In a Terraform-managed account, use `app_urls` and the bootstrap script instead,
  or the next `terraform apply` reverts the changes.
- The state contains the database admin password and the Cognito client secret. Keep it private, or move it
  to the S3 backend sketched in `versions.tf`.
- The original account isn't managed by this Terraform. To adopt it, `terraform import` each resource first
  (the defaults already match its settings).
