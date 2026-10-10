from app.core import config


def _fresh_settings(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    try:
        return config.get_settings()
    finally:
        config.get_settings.cache_clear()


def test_client_secret_is_read_from_ssm_when_only_the_parameter_is_set(monkeypatch):
    reads = []
    monkeypatch.setattr(config, "_read_secure_parameter", lambda name, region: reads.append((name, region)) or "from-ssm")

    settings = _fresh_settings(
        monkeypatch,
        COGNITO_APP_CLIENT_SECRET="",
        COGNITO_APP_CLIENT_SECRET_PARAM="/project-hub/dev/cognito-app-client-secret",
        APP_AWS_REGION="ca-central-1",
    )

    assert settings.cognito_app_client_secret == "from-ssm"
    assert reads == [("/project-hub/dev/cognito-app-client-secret", "ca-central-1")]


def test_an_explicit_client_secret_skips_ssm(monkeypatch):
    monkeypatch.setattr(config, "_read_secure_parameter", lambda name, region: (_ for _ in ()).throw(AssertionError))

    settings = _fresh_settings(
        monkeypatch,
        COGNITO_APP_CLIENT_SECRET="local-secret",
        COGNITO_APP_CLIENT_SECRET_PARAM="/project-hub/dev/cognito-app-client-secret",
    )

    assert settings.cognito_app_client_secret == "local-secret"
