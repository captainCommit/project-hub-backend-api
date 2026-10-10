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
    # Session cookies are HttpOnly + SameSite=Strict; Secure can only be relaxed for plain-http dev hosts.
    session_cookie_secure: bool = Field(default=True, validation_alias="SESSION_COOKIE_SECURE")
    auth_mode: Literal["local", "cognito"] = Field(default="local", validation_alias="AUTH_MODE")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

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


@lru_cache
def get_settings() -> Settings:
    return Settings()