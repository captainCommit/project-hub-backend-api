# AWS Deployment (dev)

Project Hub dev runs in **ca-central-1**. GitHub Actions deploys both repos; nothing is deployed from a laptop.

## Architecture

```text
https://<id>.cloudfront.net          (one CloudFront distribution, created by this repo's stack)
 ├── /api/*  → API Gateway HTTP API → Lambda (FastAPI + Mangum)  → RDS Postgres project-hub-db
 └── /*      → private S3 bucket with the UI build (uploaded by the project-hub-ui workflow)
```

The UI and API must share one site: the session cookies are `HttpOnly; Secure; SameSite=Strict`,
and `cloudfront.net` / `execute-api.amazonaws.com` are public suffixes, so separate hostnames would
be cross-site and the browser would drop the cookies. A CloudFront Function serves `index.html` for
client-side routes (any path without a file extension), so API error responses pass through untouched.
Because both share one origin, the API has CORS turned off (`CORS_ORIGINS` is empty in the Lambda).

The Lambda runs on `arm64` with 1769 MB (one full vCPU; startup is CPU-bound) and keeps CloudWatch logs for 30 days. boto3 is left out of the package
because the Lambda runtime already provides it.

If the Content-Security-Policy blocks something new (the browser console shows the blocked URL),
add the source to `SecurityHeadersPolicy` in `serverless.yml`.

The `project-hub-api-dev` stack (`serverless.yml`) owns:

| Resource | Purpose |
| --- | --- |
| Lambda + HTTP API | The FastAPI app |
| `UiBucket` | UI build, readable only by CloudFront (Origin Access Control) |
| `AttachmentsBucket` | Attachment files; the browser uploads/downloads with presigned URLs. CORS allows the app URL and `http://localhost:5173`. Retained if the stack is removed. |
| `AppDistribution` | CloudFront in front of both |
| `SecurityHeadersPolicy` | HSTS, `X-Frame-Options: DENY`, `nosniff`, referrer policy, Permissions-Policy and a Content-Security-Policy on every response |
| `OriginVerifySecret` | Secrets Manager value CloudFront sends to the API as `X-Origin-Verify`; the Lambda refuses requests without it (`ORIGIN_VERIFY_SECRET`), so the raw `execute-api` URL returns 403 |
| `AlarmTopic` + alarms | SNS topic and CloudWatch alarms: Lambda errors and throttles, API 5xx (3+ in 5 min), RDS CPU above 80% for 15 min, RDS free storage below 2 GiB |

Stack outputs: `AppUrl`, `CloudFrontDistributionId`, `UiBucketName`, `AttachmentsBucketName`.

## Workflows

| Repo | Workflow | Trigger | Does |
| --- | --- | --- | --- |
| project-hub-backend-api | `deploy-dev.yml` | push to `dev`, manual | `tests.yml` → `alembic upgrade head` → `serverless deploy --stage dev` |
| project-hub-backend-api | `tests.yml` | push to `main`, PR to `dev`/`main`, called by `deploy-dev.yml` | migrations on a fresh Postgres 18 → tests |
| project-hub-ui | `deploy-dev.yml` | push to `master`, manual | build → S3 sync → CloudFront invalidation |

Both sign in to AWS with GitHub OIDC roles; no AWS keys are stored in GitHub. Migrations run before
the new code goes live, so each migration must work with the version that is still running.

## One-time setup

1. **AWS roles** (needs an admin profile):

   ```bash
   bash scripts/setup-github-deploy-roles.sh
   ```

   Creates the GitHub OIDC provider and two roles, each assumable only by its repo's `dev` environment:
   `project-hub-api-github-deploy` and `project-hub-ui-github-deploy`.

2. **GitHub, project-hub-backend-api** → Settings → Environments → `dev`:

   | Kind | Name | Value |
   | --- | --- | --- |
   | Variable | `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::<account>:role/project-hub-api-github-deploy` |
   | Variable | `COGNITO_USER_POOL_ID` | `ca-central-1_ICne3GHp3` |
   | Variable | `COGNITO_APP_CLIENT_ID` | `5cfgg1smmi3h7bhmv19tu6hom0` |
   | Variable | `COGNITO_ADDITIONAL_CLIENT_IDS` | (empty) |
   | Variable | `COGNITO_DOMAIN` | `https://projecthub-auth.auth.ca-central-1.amazoncognito.com` |
   | Variable | `APP_DATABASE_URL` | set by `scripts/setup-db-iam-auth.sh` (no password) |
   | Secret | `DATABASE_URL` | the admin `DATABASE_URL` from your local `.env`; used only by the migration step |
   | Variable | `ALARM_EMAIL` | inbox for alarm emails (optional; AWS emails a confirmation link after the first deploy) |

   The Cognito client secret is not in GitHub: the Lambda reads it from SSM (see [Database](#database)).

3. **GitHub, project-hub-ui** → Settings → Environments → `dev`:

   | Kind | Name | Value |
   | --- | --- | --- |
   | Variable | `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::<account>:role/project-hub-ui-github-deploy` |
   | Variable | `VITE_COGNITO_DOMAIN` | `https://projecthub-auth.auth.ca-central-1.amazoncognito.com` |
   | Variable | `VITE_COGNITO_CLIENT_ID` | `5cfgg1smmi3h7bhmv19tu6hom0` |

   Old repository secrets `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` are no longer used; delete them.

4. **First deploy**: run the API workflow first (it creates the buckets and CloudFront; CloudFront
   takes ~5–10 minutes the first time), then the UI workflow.

5. **Cognito**: allow the new app URL for hosted sign-in (copies the client config, only adds URLs):

   ```bash
   python scripts/add_cognito_app_url.py https://<id>.cloudfront.net
   ```

6. **Local dev**: set `S3_BUCKET_NAME` in `.env` to the `AttachmentsBucketName` output.

## Database

RDS `project-hub-db` (Postgres 18, db.t4g.micro) is publicly reachable on 5432 because the Lambda
does not run inside a VPC (no NAT gateway). It is protected by:

- **SSL with certificate checks.** RDS rejects unencrypted connections (`rds.force_ssl=1`), and every
  connection to an `*.rds.amazonaws.com` host uses `sslmode=verify-full` against the bundled
  `app/certs/rds-global-bundle.pem`, so a spoofed server is refused.
- **No password in the Lambda.** It signs in as `project_hub_app` (read/write on app tables, no DDL)
  with a 15-minute IAM token minted per connection (`DATABASE_IAM_AUTH=true`, `rds-db:connect` on the
  Lambda role). The admin password exists only in `.env` and the GitHub `DATABASE_URL` secret, which
  only the migration step sees.
- **Cognito client secret in SSM.** The Lambda reads the SecureString
  `/project-hub/<stage>/cognito-app-client-secret` at startup (`COGNITO_APP_CLIENT_SECRET_PARAM`).
  Locally, `COGNITO_APP_CLIENT_SECRET` in `.env` still works and takes precedence.

One-time setup (before the first deploy that uses IAM auth):

```bash
bash scripts/setup-db-iam-auth.sh
```

Run a migration by hand only when needed (the deploy workflow normally does it):

```bash
alembic upgrade head   # uses DATABASE_URL from .env or the environment
```

## Users and accounts

The Cognito pool is invite-only. Adding a user (Account → Users, or the invite API) creates them in
Cognito with `AdminCreateUser`, which emails a temporary password from Cognito's default sender
(limit: 50 emails a day). Their Project Hub user is linked by email on first sign-in.

**Platform admins** (`users.is_platform_admin`) create accounts and can add users to any account
(Account → All Accounts). Creating an account with an admin email invites that person as its ADMIN;
without one, the creator becomes OWNER. Grant or revoke it with the admin `DATABASE_URL`:

```bash
python scripts/grant_platform_admin.py prjadmin@projecthub.com          # --revoke to remove
```

## Smoke test

```bash
curl https://<id>.cloudfront.net/api/v1/... # any API route, through CloudFront
aws cloudformation describe-stacks --region ca-central-1 --stack-name project-hub-api-dev \
  --query "Stacks[0].Outputs" --output table
```

## Packaging locally

Lambda needs Linux arm64 wheels (`psycopg[binary]`, `cryptography`, `pydantic-core`). pip downloads
them directly (`--platform=manylinux2014_aarch64 --only-binary=:all:` in `pipCmdExtraArgs`), so
`serverless package` works on any OS without Docker. A new dependency must publish an aarch64 or
pure-Python wheel. `npx serverless print --stage dev` checks the resolved configuration without packaging.

## Remove the stack

```bash
npx serverless remove --stage dev
```

Empty the UI bucket first. The attachments bucket, RDS and Cognito are kept.
