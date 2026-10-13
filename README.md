# Project Hub API

FastAPI backend for Project Hub.

## Tech stack

- Python 3.11+
- FastAPI
- Mangum
- SQLAlchemy 2.x
- Alembic
- Pydantic Settings
- PostgreSQL
- psycopg
- pytest
- Serverless Framework
- AWS Lambda/API Gateway HTTP API

## Setup

Create a local virtual environment:

```bash
python -m venv .venv
```

Activate it on macOS/Linux:

```bash
source .venv/bin/activate
```

Activate it on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Activate it on Windows Command Prompt:

```bat
.venv\Scripts\activate.bat
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a local environment file:

```bash
cp .env.example .env
```

On Windows Command Prompt:

```bat
copy .env.example .env
```

## Database migrations

Start the local PostgreSQL database with Docker Compose:

```bash
docker compose up -d
```

This starts PostgreSQL 16 with:

- Database: `project_hub`
- Username: `postgres`
- Password: `postgres`
- Port: `5432`

Run migrations:

```bash
alembic upgrade head
```

Create a new migration after changing SQLAlchemy models:

```bash
alembic revision --autogenerate -m "describe change"
```

## Run locally

```bash
uvicorn app.main:app --reload
```

The API will be available at:

- Root: `http://127.0.0.1:8000/`
- Health: `http://127.0.0.1:8000/health`
- OpenAPI docs: `http://127.0.0.1:8000/docs`
- Swagger UI alias: `http://127.0.0.1:8000/swagger`
- Swagger/OpenAPI JSON: `http://127.0.0.1:8000/swagger.json`

## Swagger/OpenAPI

The API exposes interactive Swagger documentation and a machine-readable OpenAPI schema:

- `GET /swagger` - interactive Swagger UI for trying the APIs from a browser.
- `GET /swagger.json` - OpenAPI schema JSON that can be imported into API clients or documentation tooling.
- `GET /docs` and `GET /openapi.json` remain available as the default FastAPI documentation endpoints.

Protected endpoints use bearer authentication in the generated OpenAPI schema. In Swagger UI, click **Authorize** and enter your Cognito JWT when `AUTH_MODE=cognito`.

## Authentication

The API supports two authentication modes controlled by `AUTH_MODE`.

### Local auth mode

Use local mode for local development and tests:

```env
ENVIRONMENT=local
AUTH_MODE=local
```

The local bypass is only allowed when `ENVIRONMENT=local`. Non-local environments should use `AUTH_MODE=cognito`

In local mode, the API bypasses Cognito and uses a development user:

```text
dev@example.com / Dev User
```

Calling `GET /api/v1/me` will create this user if it does not already exist.

### Cognito auth mode

Use Cognito mode when authenticating requests with AWS Cognito JWTs:

```env
AUTH_MODE=cognito
AWS_REGION=ca-central-1
COGNITO_USER_POOL_ID=your-user-pool-id
COGNITO_APP_CLIENT_ID=your-app-client-id
COGNITO_ADDITIONAL_CLIENT_IDS=
COGNITO_DOMAIN=https://your-domain.auth.ca-central-1.amazoncognito.com
COGNITO_APP_CLIENT_SECRET=your-app-client-secret
```

`COGNITO_APP_CLIENT_ID` is the primary app client. Set `COGNITO_ADDITIONAL_CLIENT_IDS` (comma-separated) to also accept tokens from other app clients, such as the browser UI's public client (no secret, hosted login with Authorization Code + PKCE). The UI sends the ID token, which carries the `email` and `name` claims used to sync the local user.

When `AUTH_MODE=cognito`, protected endpoints require a bearer token:

```bash
curl http://127.0.0.1:8000/api/v1/me \
  -H "Authorization: Bearer <cognito-jwt>"
```

The API validates:

- JWT signature using the Cognito JWKS endpoint
- issuer
- audience/client ID
- expiration
- `token_use`, which must be `id` or `access`

On the first valid Cognito request, the API syncs a local `users` row from token claims:

- `sub` -> `cognito_sub`
- `email` -> `email`
- `name` -> `full_name`, when available

If the Cognito user's email or name changes later, the local user record is updated on the next authenticated request.

### Browser sessions (cookies)

The web UI never handles tokens. Sign-in uses the Cognito hosted login (Authorization Code + PKCE):

1. The UI redirects to `COGNITO_DOMAIN/oauth2/authorize` with a PKCE challenge.
2. Cognito redirects back to the UI's `/auth/callback`, which posts the code and PKCE verifier to `POST /api/v1/auth/session`.
3. The API exchanges the code using `COGNITO_APP_CLIENT_SECRET`, validates the ID token, and sets two cookies:
   - `ph_session` — the ID token, `HttpOnly; Secure; SameSite=Strict; Path=/api`, expires with the token (60 min).
   - `ph_refresh` — the refresh token, `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` (5 days).
4. `POST /api/v1/auth/refresh` renews `ph_session`; `POST /api/v1/auth/logout` revokes the refresh token and clears both cookies.

Requests authenticated by cookie must send `X-Requested-With` on writes (CSRF defence in addition to SameSite). Bearer-token clients are unaffected.

**Same-site requirement:** `SameSite=Strict` cookies are only sent when the UI and API are on the same site. In development the UI's Vite server proxies `/api` to the API. In production serve both from one domain, e.g. a CloudFront distribution with the UI as the default origin and `/api/*` routed to API Gateway. Add that domain's `/auth/callback` and `/` to the app client's callback and sign-out URLs.

Auth failures return specific `error_code`s the UI acts on: `AUTH_REQUIRED`, `TOKEN_EXPIRED`, `TOKEN_INVALID`, `SESSION_EXPIRED`, `LOGIN_FAILED`, `USER_DISABLED`, `ACCOUNT_CONFLICT`, `CSRF_CHECK_FAILED`, `AUTH_UNAVAILABLE` (503), `AUTH_NOT_CONFIGURED` (503).

## Run tests

```bash
pytest
```

## S3 attachments

The API supports generic file attachments by storing attachment metadata in PostgreSQL and returning S3 pre-signed URLs for direct browser/client upload and download. Files are not streamed through FastAPI or Lambda.

Configure attachments with:

```env
AWS_REGION=ca-central-1
S3_BUCKET_NAME=your-attachments-bucket
ATTACHMENT_UPLOAD_EXPIRES_SECONDS=900
ATTACHMENT_DOWNLOAD_EXPIRES_SECONDS=900
```

The application IAM role/user needs permission to generate pre-signed URLs and delete objects in the configured bucket. At minimum, allow `s3:PutObject`, `s3:GetObject`, and `s3:DeleteObject` for keys under the bucket prefix used by the API:

```text
accounts/{account_id}/{entity_type}/{entity_id}/{attachment_id}/{safe_file_name}
```

Clients should call `POST /api/v1/entities/{entity_type}/{entity_id}/attachments/presigned-upload`, upload directly to the returned `upload_url` with the returned method/headers, and later call `GET /api/v1/attachments/{attachment_id}/presigned-download` to retrieve a download URL.

## Phase 0 Verification

Use these steps to verify Phase 0 end-to-end against the local PostgreSQL database:

1. Start PostgreSQL:

   ```bash
   docker compose up -d
   ```

2. Apply database migrations:

   ```bash
   alembic upgrade head
   ```

3. Start the API:

   ```bash
   uvicorn app.main:app --reload
   ```

4. Confirm the expected tables exist:

   ```bash
   docker compose exec postgres psql -U postgres -d project_hub -c "\dt"
   ```

   You should see both `health_checks` and `alembic_version`.

5. Confirm the root endpoint works:

   ```bash
   curl http://127.0.0.1:8000/
   ```

   Expected response:

   ```json
   {"message":"Project Hub API is running"}
   ```

6. Confirm the health endpoint reports a connected database:

   ```bash
   curl http://127.0.0.1:8000/health
   ```

   Expected response:

   ```json
   {"status":"ok","database":"connected"}
   ```

## Connecting to AWS PostgreSQL

The app connects to PostgreSQL through the `DATABASE_URL` environment variable, so the same code works with a remote AWS PostgreSQL database such as Amazon RDS for PostgreSQL or Aurora PostgreSQL.

Example AWS RDS connection string:

```env
DATABASE_URL=postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub
```

If your AWS database requires SSL, add the SSL mode query parameter:

```env
DATABASE_URL=postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub?sslmode=require
```

Before running migrations or the API against AWS RDS, confirm:

- The RDS/Aurora database exists and is PostgreSQL-compatible.
- The database name is `project_hub`, or `DATABASE_URL` is updated to the actual database name.
- The RDS security group allows inbound PostgreSQL traffic on port `5432` from your current IP address, VPN, bastion host, CI runner, or Lambda/network location.
- The database credentials are stored securely. Do not commit real AWS credentials or production database URLs to git.
- For deployed AWS Lambda usage, store `DATABASE_URL` in environment variables or AWS Secrets Manager, not in source code.

To run migrations against AWS RDS from your local machine:

```bash
export DATABASE_URL="postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub?sslmode=require"
alembic upgrade head
```

On Windows PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub?sslmode=require"
alembic upgrade head
```

Then start the API with the same `DATABASE_URL` set:

```bash
uvicorn app.main:app --reload
```

`GET /health` should return:

```json
{"status":"ok","database":"connected"}
```

## Billing (Stripe)

Accounts are billed per seat per month: owners, managers and members at `SEAT_PRICE_CENTS`, viewers at
`VIEWER_SEAT_PRICE_CENTS` (CAD by default). A seat is an active member who has signed in; pending
invites are free. Billing is off for every account until a platform admin turns it on in the admin portal.

Setup, once per Stripe mode (test first, then live):

1. Put the secret key in `.env` as `STRIPE_SECRET_KEY` and run `python scripts/setup_stripe_prices.py`.
   Copy the two printed price IDs into `STRIPE_FULL_SEAT_PRICE_ID` and `STRIPE_VIEWER_SEAT_PRICE_ID`.
2. In Stripe, add a webhook endpoint for `<APP_URL>/api/v1/billing/stripe-webhook` with the events
   `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.created`, `invoice.finalized`, `invoice.updated`,
   `invoice.paid`, `invoice.payment_failed`, `invoice.voided`, `invoice.marked_uncollectible` and
   `invoice.upcoming`. Put its signing secret in `STRIPE_WEBHOOK_SECRET`.
3. In Stripe's subscription settings, set "if all retries for a payment fail" to **mark the subscription
   as unpaid**. An unpaid or cancelled subscription makes the account read-only until it is paid.
4. Enable the customer portal (Settings, Billing, Customer portal) so owners can update cards and see invoices.
5. Set `APP_URL` to the UI's address so Checkout and the portal can send people back.

Deployed, the two secrets live in SSM SecureStrings `/project-hub/<stage>/stripe-secret-key` and
`/project-hub/<stage>/stripe-webhook-secret`; the price IDs and `APP_URL` are GitHub `dev` environment
variables. Until the secrets exist, the API starts normally and billing reports that payments aren't set up.
For local webhook testing, `stripe listen --forward-to localhost:8000/api/v1/billing/stripe-webhook`
prints a signing secret to use.

## Notification emails (SES)

Every in-app notification can also be emailed. Each person picks, per category (mentions, assignments,
due soon, status changes, comments), **right away**, **daily digest** or **off** under Account > Notifications,
plus the hour and timezone for their digest. Every email links to the item and carries a signed unsubscribe
link (and one-click `List-Unsubscribe` headers).

How it runs:

- `NotificationService.create_notification` only marks the notification's `email_status` (`PENDING` or
  `DIGEST`) in the same transaction. Nothing is sent during the request.
- `app/worker.py` runs as separate Lambdas: `notificationEmails` every minute sends `PENDING` emails;
  `morningNotifications` hourly creates `DUE_SOON` reminders (tasks assigned to the person, due today or
  tomorrow, from the My work due-tasks query) and sends digests once each person's local digest hour has come.
- At send time the worker re-checks the person's settings, account membership, access to the item, and the
  `email_suppressions` table. Failed sends are retried up to 5 times.
- SES publishes hard bounces and complaints through the `project-hub-api-<stage>` configuration set to SNS;
  `sesEvents` adds those addresses to `email_suppressions`.

Email stays off until `EMAIL_FROM` is set (with `EMAIL_TOKEN_SECRET` and `APP_URL`). Setup:

1. In SES (ca-central-1), verify the sender: a domain identity with the DKIM CNAMEs, plus SPF and a DMARC
   record on that domain. A single verified email address works for testing.
2. Request production access for SES. In the sandbox, SES only delivers to verified addresses.
3. Set the GitHub `dev` environment variable `EMAIL_FROM`, e.g. `Project Hub <notifications@your-domain>`, and
   deploy. The stack creates the token secret (`/project-hub/<stage>/email-token`), the configuration set, and
   the SNS topic.

Locally, set `EMAIL_FROM`, `EMAIL_TOKEN_SECRET` and `APP_URL` in `.env`, then run the jobs by hand, e.g.
`python -c "from app.worker import deliver_handler; print(deliver_handler({}, None))"` (uses your AWS
credentials to call SES).

## AI assistant

The assistant (the AIAssistant page and the floating widget) answers questions about project data. It is not
tied to one AI company: each account uses either its **own model key** or, if it has none, a **model Project Hub
provides**. It only reads, and only what the person asking can see: its tools call the same services as the API
endpoints, as that person (`app/services/ai/tools.py`).

Providers (`app/services/ai/providers.py`): OpenAI, Anthropic, Google Gemini, Azure OpenAI, and any
OpenAI-compatible endpoint (OpenRouter, Groq, Mistral, self-hosted Ollama or vLLM). Two small adapters cover
them: OpenAI Chat Completions and Anthropic Messages. The model must support tool calling.

How a question is answered:

- `POST /api/v1/ai/conversations/{id}/messages` stores the question, queues a run and returns 202 at once.
- The `aiAssistant` Lambda (`app.worker.ai_run_handler`), started with an asynchronous invoke, calls the model
  and the tools until it answers: at most `AI_MAX_MODEL_CALLS` (8) calls and `AI_RUN_TIMEOUT_SECONDS` (120).
  The chat polls the conversation while the run is active. Locally (no `AI_WORKER_FUNCTION`) the question is
  answered in-process after the response is sent.
- `aiHousekeeping` runs hourly: fails runs whose worker died, deletes conversations past the account's
  retention (owners choose; 90 days by default), and reports platform usage to Stripe.

**Own keys (BYOK).** Owners connect a provider under Account > AI assistant. Saving sends one tiny request that
must come back as a tool call. Keys are encrypted with the `AiKeysKmsKey` KMS key, with the account id as
encryption context; the API role can only encrypt and only `AiWorkerRole` can decrypt. Locally,
`AI_LOCAL_KEY_SECRET` stands in for KMS (refused outside `ENVIRONMENT=local`).

**Platform models.** An owner requests Project Hub's model, accepting that usage past the free allowance is
billed. A platform admin (Admin > AI) adds the models with their cost per million tokens and a margin, then
switches the model on per account with an allowance (free tokens or a credit, default from
`AI_DEFAULT_ALLOWANCE_KIND` / `AI_DEFAULT_ALLOWANCE_AMOUNT`) and an optional monthly cap. An account's own key
always wins, and a broken own key never falls back to the platform model. The platform's provider keys are
SSM SecureStrings, never in the database:

    aws ssm put-parameter --type SecureString --name /project-hub/dev/ai/openai-api-key --value sk-...
    aws ssm put-parameter --type SecureString --name /project-hub/dev/ai/anthropic-api-key --value sk-ant-...

Locally set `AI_PLATFORM_OPENAI_API_KEY` / `AI_PLATFORM_ANTHROPIC_API_KEY` / `AI_PLATFORM_GEMINI_API_KEY` instead.

**Billing platform usage.** Each platform run stores its charge (cost plus margin). Usage past the allowance is
invoiced through a Stripe billing meter, 1 unit = 1 cent:

1. Run `python scripts/setup_stripe_ai_usage.py` and put the printed ID in `STRIPE_AI_USAGE_PRICE_ID`
   (GitHub `dev` variable when deployed).
2. New subscriptions get the metered line at checkout; existing ones get it when a platform admin switches the
   model on, or at the next hourly report.
3. Every hour, and on Stripe's `invoice.upcoming`, only the whole cents not yet reported are sent; event
   identifiers make retries safe.

Without the price ID (or a live subscription), usage can't be invoiced, so the allowance is the limit and the
assistant pauses until the next month.

## AWS Lambda

The application exposes a Mangum handler in `app/main.py`:

```python
handler = Mangum(app)
```

Phase 9 deploys this handler to AWS Lambda behind API Gateway HTTP API with the Serverless Framework. See the full deployment guide in [`docs/deployment.md`](docs/deployment.md).

Install the Serverless tooling locally:

```bash
npm install
```

Set the required deployment environment variables before packaging or deploying:

```env
APP_NAME=Project Hub API
ENVIRONMENT=dev
DATABASE_URL=postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub?sslmode=require
AUTH_MODE=cognito
AWS_REGION=ca-central-1
COGNITO_USER_POOL_ID=your-user-pool-id
COGNITO_APP_CLIENT_ID=your-app-client-id
COGNITO_ADDITIONAL_CLIENT_IDS=
COGNITO_DOMAIN=https://your-domain.auth.ca-central-1.amazoncognito.com
COGNITO_APP_CLIENT_SECRET=your-app-client-secret
S3_BUCKET_NAME=your-attachments-bucket
```

Build a deployment artifact:

```bash
npm run sls:package -- --stage dev
```

Deploy to AWS:

```bash
npm run sls:deploy -- --stage dev
```

Alembic migrations are not run automatically by CI/CD yet. Run them manually from a machine or runner that can reach the target database:

```bash
export DATABASE_URL="postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub?sslmode=require"
alembic upgrade head
```

On Windows PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub?sslmode=require"
alembic upgrade head
```

The included GitHub Actions workflow runs `pytest` only; it does not deploy and does not run migrations.