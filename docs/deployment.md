# AWS Lambda/API Gateway Deployment

Phase 9 deploys the Project Hub FastAPI API to AWS Lambda with Mangum and exposes it through API Gateway HTTP API using the Serverless Framework.

## Architecture

```text
FastAPI app (`app.main:app`)
  -> Mangum handler (`app.main:handler`)
  -> AWS Lambda (`project-hub-api-<stage>-api`)
  -> API Gateway HTTP API
```

The deployment entrypoint is already defined in `app/main.py`:

```python
handler = Mangum(app)
```

## Prerequisites

- Python 3.11+
- Node.js/npm
- AWS credentials configured for the target account (`aws configure`, SSO, or environment variables)
- An AWS PostgreSQL database such as RDS or Aurora PostgreSQL
- A Cognito user pool/app client when deploying with `AUTH_MODE=cognito`
- An S3 bucket for attachments
- Serverless Framework dependencies installed locally with `npm install`

The Lambda execution role created by `serverless.yml` includes object-level access to the configured S3 bucket for presigned attachment uploads, downloads, and deletes.

## Required environment variables

Set these values before packaging or deploying:

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

The local `.env` file is loaded by Serverless because `serverless.yml` sets `useDotenv: true`. Do not commit `.env` or real credentials.

## Install deployment tooling

```bash
pip install -r requirements.txt
npm install
```

## Validate locally before deployment

Run the Python test suite:

```bash
pytest
```

Build a Serverless deployment artifact:

```bash
npm run sls:package -- --stage dev
```

This writes the packaged artifact to `.serverless/`.

### Native dependency packaging note

The API uses PostgreSQL dependencies that include native wheels. For a production Lambda artifact, package/deploy from Linux or use Docker-based packaging on macOS/Windows:

```bash
SLS_DOCKERIZE_PIP=true npm run sls:package -- --stage dev
```

On Windows PowerShell:

```powershell
$env:SLS_DOCKERIZE_PIP="true"
npm run sls:package -- --stage dev
```

If Docker is not installed, leave `SLS_DOCKERIZE_PIP` unset for a local packaging smoke test, then create the production artifact from a Linux environment.

## Deploy

Deploy to a named stage:

```bash
npm run sls:deploy -- --stage dev
```

After deployment, get the HTTP API endpoint:

```bash
npx serverless info --stage dev
```

Smoke test the deployed API:

```bash
curl https://<api-id>.execute-api.<region>.amazonaws.com/
curl https://<api-id>.execute-api.<region>.amazonaws.com/health
```

Protected endpoints require a Cognito bearer token when `AUTH_MODE=cognito`.

## Manual database migrations

Alembic migrations are intentionally **not** run automatically by CI/CD in Phase 9. Run migrations manually from a machine or runner that can reach the target database.

macOS/Linux:

```bash
export DATABASE_URL="postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub?sslmode=require"
alembic upgrade head
```

Windows PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:your-password@your-rds-endpoint.region.rds.amazonaws.com:5432/project_hub?sslmode=require"
alembic upgrade head
```

Run the migration before deploying code that depends on new schema changes, or during a planned maintenance window when the schema change is not backward compatible.

## GitHub Actions

The included workflow at `.github/workflows/tests.yml` runs tests only. It does not package, deploy, or run Alembic migrations.

## Remove the stack

To remove the deployed Lambda/API Gateway stack for a stage:

```bash
npm run sls:remove -- --stage dev
```

This does not delete external resources such as RDS databases, Cognito user pools, or S3 buckets.