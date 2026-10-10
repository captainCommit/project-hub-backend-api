"""Allow a deployed app URL to use Cognito hosted sign-in (callback + sign-out URLs).

UpdateUserPoolClient resets every setting that is not passed, so this copies the current
client configuration and only appends the new URLs. Safe to re-run.

Usage: python scripts/add_cognito_app_url.py https://dxxxx.cloudfront.net
Reads COGNITO_USER_POOL_ID / COGNITO_APP_CLIENT_ID / APP_AWS_REGION from the environment or .env.
"""
import sys
from pathlib import Path

import boto3

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import get_settings  # noqa: E402

UPDATABLE_FIELDS = (
    "ClientName", "RefreshTokenValidity", "AccessTokenValidity", "IdTokenValidity", "TokenValidityUnits",
    "ReadAttributes", "WriteAttributes", "ExplicitAuthFlows", "SupportedIdentityProviders", "CallbackURLs",
    "LogoutURLs", "DefaultRedirectURI", "AllowedOAuthFlows", "AllowedOAuthScopes",
    "AllowedOAuthFlowsUserPoolClient", "AnalyticsConfiguration", "PreventUserExistenceErrors",
    "EnableTokenRevocation", "EnablePropagateAdditionalUserContextData", "AuthSessionValidity",
    "RefreshTokenRotation",
)


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("Usage: python scripts/add_cognito_app_url.py https://<app-host>")
    app_url = sys.argv[1].rstrip("/")
    settings = get_settings()
    client = boto3.client("cognito-idp", region_name=settings.aws_region)

    current = client.describe_user_pool_client(
        UserPoolId=settings.cognito_user_pool_id, ClientId=settings.cognito_app_client_id
    )["UserPoolClient"]
    update = {field: current[field] for field in UPDATABLE_FIELDS if field in current}
    update["CallbackURLs"] = sorted({*current.get("CallbackURLs", []), f"{app_url}/auth/callback"})
    update["LogoutURLs"] = sorted({*current.get("LogoutURLs", []), f"{app_url}/"})

    client.update_user_pool_client(
        UserPoolId=settings.cognito_user_pool_id, ClientId=settings.cognito_app_client_id, **update
    )
    print("Callback URLs:", ", ".join(update["CallbackURLs"]))
    print("Sign-out URLs:", ", ".join(update["LogoutURLs"]))


if __name__ == "__main__":
    main()
