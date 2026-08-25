"""Application startup and configuration smoke tests."""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.main import create_app


def test_create_app_returns_configured_application() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "RazorShield AI"
    assert app.version == "0.1.0"


def test_health_route_is_registered() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/health" in paths
    assert "/health/db" in paths


def test_openapi_schema_builds(client) -> None:  # noqa: ANN001 - pytest fixture
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "RazorShield AI"


def test_settings_are_cached() -> None:
    assert get_settings() is get_settings()


def test_cors_origins_are_parsed_from_a_comma_separated_string() -> None:
    settings = Settings(cors_origins="http://a.test, http://b.test ,")
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_no_secret_is_baked_into_the_default_llm_configuration() -> None:
    assert Settings.model_fields["llm_api_key"].default is None


def test_a_blank_llm_api_key_is_treated_as_unset() -> None:
    assert Settings(llm_api_key="").llm_api_key is None
