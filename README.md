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

The local bypass is only allowed when `ENVIRONMENT=local`. Non-local environments should use `AUTH_MODE=cognito`.

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
```

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