import logging
from functools import lru_cache

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="Project Hub API", validation_alias="APP_NAME")
    environment: str = Field(default="local", validation_alias="ENVIRONMENT")
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/project_hub",
        validation_alias="DATABASE_URL",
    )
    # Sign in to RDS with a short-lived IAM token instead of a password (the deployed Lambda does this).
    database_iam_auth: bool = Field(default=False, validation_alias="DATABASE_IAM_AUTH")
    cors_origins: str = Field(
        default="http://localhost:3000",
        validation_alias="CORS_ORIGINS",
    )
    aws_region: str = Field(default="ca-central-1", validation_alias=AliasChoices("APP_AWS_REGION", "AWS_REGION"))
    s3_bucket_name: str = Field(default="", validation_alias="S3_BUCKET_NAME")
    attachment_upload_expires_seconds: int = Field(
        default=900,
        validation_alias="ATTACHMENT_UPLOAD_EXPIRES_SECONDS",
    )
    attachment_download_expires_seconds: int = Field(
        default=900,
        validation_alias="ATTACHMENT_DOWNLOAD_EXPIRES_SECONDS",
    )
    cognito_user_pool_id: str = Field(default="", validation_alias="COGNITO_USER_POOL_ID")
    cognito_app_client_id: str = Field(default="", validation_alias="COGNITO_APP_CLIENT_ID")
    # Extra app clients whose tokens are accepted, e.g. the browser UI's public client.
    cognito_additional_client_ids: str = Field(default="", validation_alias="COGNITO_ADDITIONAL_CLIENT_IDS")
    cognito_invite_enabled: bool = Field(default=False, validation_alias="COGNITO_INVITE_ENABLED")
    # Hosted login domain and client secret: the API exchanges sign-in codes and refresh tokens.
    cognito_domain: str = Field(default="", validation_alias="COGNITO_DOMAIN")
    cognito_app_client_secret: str = Field(default="", validation_alias="COGNITO_APP_CLIENT_SECRET")
    # Deployed: the secret lives in this SSM SecureString and is read at startup, not in the Lambda environment.
    cognito_app_client_secret_param: str = Field(default="", validation_alias="COGNITO_APP_CLIENT_SECRET_PARAM")
    # Session cookies are HttpOnly + SameSite=Strict; Secure can only be relaxed for plain-http dev hosts.
    session_cookie_secure: bool = Field(default=True, validation_alias="SESSION_COOKIE_SECURE")
    auth_mode: Literal["local", "cognito"] = Field(default="local", validation_alias="AUTH_MODE")
    # Deployed: CloudFront adds X-Origin-Verify with this value, so calls straight to execute-api are refused.
    # Empty (local dev) turns the check off.
    origin_verify_secret: str = Field(default="", validation_alias="ORIGIN_VERIFY_SECRET")
    # Monthly price per seat, in cents. Viewers are billed at their own (lower) rate; every other role at the full rate.
    seat_price_cents: int = Field(default=2000, validation_alias="SEAT_PRICE_CENTS")
    viewer_seat_price_cents: int = Field(default=1000, validation_alias="VIEWER_SEAT_PRICE_CENTS")
    billing_currency: str = Field(default="CAD", validation_alias="BILLING_CURRENCY")
    # Stripe. Without a secret key, billing stays visible but nothing can be charged.
    stripe_secret_key: str = Field(default="", validation_alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str = Field(default="", validation_alias="STRIPE_WEBHOOK_SECRET")
    # Deployed: both secrets live in SSM SecureStrings and are read at startup, like the Cognito secret.
    stripe_secret_key_param: str = Field(default="", validation_alias="STRIPE_SECRET_KEY_PARAM")
    stripe_webhook_secret_param: str = Field(default="", validation_alias="STRIPE_WEBHOOK_SECRET_PARAM")
    # The two recurring monthly prices, created by scripts/setup_stripe_prices.py.
    stripe_full_seat_price_id: str = Field(default="", validation_alias="STRIPE_FULL_SEAT_PRICE_ID")
    stripe_viewer_seat_price_id: str = Field(default="", validation_alias="STRIPE_VIEWER_SEAT_PRICE_ID")
    # Where Stripe Checkout and the billing portal send people back to (the UI's origin).
    app_url: str = Field(default="http://localhost:3000", validation_alias="APP_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_full_seat_price_id and self.stripe_viewer_seat_price_id)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_allowed_client_ids(self) -> set[str]:
        extra = [client_id.strip() for client_id in self.cognito_additional_client_ids.split(",") if client_id.strip()]
        return {client_id for client_id in [self.cognito_app_client_id, *extra] if client_id}

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"


def _read_secure_parameter(name: str, region_name: str) -> str:
    import boto3

    response = boto3.client("ssm", region_name=region_name).get_parameter(Name=name, WithDecryption=True)
    return response["Parameter"]["Value"]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.cognito_app_client_secret_param and not settings.cognito_app_client_secret:
        settings.cognito_app_client_secret = _read_secure_parameter(
            settings.cognito_app_client_secret_param, settings.aws_region
        )
    if settings.stripe_secret_key_param and not settings.stripe_secret_key:
        settings.stripe_secret_key = _read_optional_parameter(settings.stripe_secret_key_param, settings.aws_region)
    if settings.stripe_webhook_secret_param and not settings.stripe_webhook_secret:
        settings.stripe_webhook_secret = _read_optional_parameter(settings.stripe_webhook_secret_param, settings.aws_region)
    return settings


def _read_optional_parameter(name: str, region_name: str) -> str:
    """Like _read_secure_parameter, but a missing parameter means "not set up yet" rather than a failed start."""
    try:
        return _read_secure_parameter(name, region_name)
    except Exception:
        logging.getLogger(__name__).warning("SSM parameter %s could not be read; leaving it unset", name)
        return ""