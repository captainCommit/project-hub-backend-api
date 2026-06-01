from functools import lru_cache
from typing import Any, Protocol

from app.core.config import Settings


class CognitoIdentityProviderClient(Protocol):
    exceptions: Any

    def admin_create_user(self, **kwargs: Any) -> dict[str, Any]: ...


@lru_cache
def get_cognito_idp_client(region_name: str) -> CognitoIdentityProviderClient:
    import boto3

    return boto3.client("cognito-idp", region_name=region_name)


def should_send_cognito_invite(settings: Settings) -> bool:
    return (
        settings.auth_mode == "cognito"
        and settings.cognito_invite_enabled
        and bool(settings.cognito_user_pool_id)
    )


def admin_create_user_invite(
    *,
    email: str,
    full_name: str | None,
    settings: Settings,
    client: CognitoIdentityProviderClient | None = None,
) -> bool:
    cognito_client = client or get_cognito_idp_client(settings.aws_region)
    attributes = [
        {"Name": "email", "Value": email},
        {"Name": "email_verified", "Value": "true"},
    ]
    if full_name:
        attributes.append({"Name": "name", "Value": full_name})

    try:
        cognito_client.admin_create_user(
            UserPoolId=settings.cognito_user_pool_id,
            Username=email,
            UserAttributes=attributes,
            DesiredDeliveryMediums=["EMAIL"],
        )
    except cognito_client.exceptions.UsernameExistsException:
        return False
    except Exception as exc:
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if error_code == "UsernameExistsException":
            return False
        raise

    return True