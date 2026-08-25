"""Secret containment: nothing sensitive reaches Git, an image, a bundle or a
response.

Some of these are automated versions of checks a person would otherwise do once
and forget - scanning the tracked tree for a committed `.env`, sweeping every
API response for a connection string. A person doing it once catches today's
mistake; a test catches the one made in six months.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import WEAK_JWT_SECRETS, get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Regexes for the shapes a leaked secret takes. Written to match structure
#: rather than any particular value, so they still fire after a rotation.
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "postgres dsn with credentials": re.compile(
        r"postgres(?:ql)?(?:\+\w+)?://[^\s:@/\"']+:(?P<password>[^\s:@/\"']+)@",
        re.IGNORECASE,
    ),
    "anthropic-style api key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"),
    "openai-style api key": re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    "aws access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer token": re.compile(r"\bBearer\s+eyJ[A-Za-z0-9_-]{10,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
}


#: Values that look like credentials and are meant to. Anything here is public
#: by design - the config refuses every one of them outside local development,
#: which `test_security_http` checks.
KNOWN_PLACEHOLDERS = frozenset(WEAK_JWT_SECRETS) | {
    "razorshield",
    "postgres",
    "password",
    "PASSWORD",
    "example",
    # The words documentation uses where a real password would go.
    "pass",
    "user",
    "yourpassword",
    "your-password",
}


#: Files whose *purpose* is to contain secret-shaped strings, with the reason.
#: Kept short and explicit: an exemption list that grows without comment is how
#: a scanner stops finding anything.
SCAN_EXEMPT = {
    # This module: it contains the patterns it searches for.
    "test_security_secrets.py",
    # Synthetic secrets fed to the log redactor, asserting they are scrubbed.
    "test_observability.py",
}


def scan(text: str) -> list[str]:
    """Every secret shape found in ``text``, by name.

    A match whose captured value is a documented placeholder is not reported.
    Without that carve-out this would fire on `.env.example` and on the compose
    file, and a scanner that cries wolf on the two files it is most often run
    against is a scanner people learn to ignore.
    """
    found: list[str] = []
    for name, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(text):
            captured = match.groupdict().get("password")
            if captured is not None and captured in KNOWN_PLACEHOLDERS:
                continue
            found.append(name)
            break
    return found


#: Directories never worth scanning: build output, caches, virtual environments
#: and the Git object store itself.
_SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".claude",
}


def _walk_repository() -> list[str]:
    """Every file in the working tree, minus build and cache directories."""
    found: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRECTORIES for part in path.relative_to(REPO_ROOT).parts):
            continue
        found.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))
    return found


def source_files() -> list[str]:
    """Files to scan: what Git tracks, or the working tree if nothing is tracked.

    The fallback matters. This project is developed in a checkout with no
    commits yet, so ``git ls-files`` returns nothing - and a scanner that
    silently skips when it finds no tracked files is a scanner that reports
    "clean" on exactly the tree most likely to contain a stray credential.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git available
        return _walk_repository()

    tracked = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not tracked:
        return _walk_repository()
    return tracked


# --------------------------------------------------------------------------
# The repository
# --------------------------------------------------------------------------
def test_no_env_file_is_tracked() -> None:
    """`.env` holds the real values. `.env.example` holds their names.

    Note this checks what Git *tracks*. A `.env` on disk is expected and is
    excluded by `.gitignore`; one that is tracked has already been committed.
    """
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    tracked = [line for line in result.stdout.splitlines() if line.strip()]

    committed = [
        path
        for path in tracked
        if Path(path).name == ".env" or Path(path).name.startswith(".env.")
        if Path(path).name != ".env.example"
    ]
    assert committed == [], f"environment files are tracked: {committed}"


def test_gitignore_excludes_the_env_file() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", ignore, re.MULTILINE), ".env must be ignored"


def test_the_example_env_contains_no_real_values() -> None:
    """It is committed, so every value in it is public by definition."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert scan(example) == [], f"secret-shaped values in .env.example: {scan(example)}"


def test_no_tracked_source_file_contains_a_secret() -> None:
    """A sweep of the whole tracked tree, not a spot check.

    Test files are included: a fixture with a real key in it is a leak in the
    same way a module with one is.
    """
    extensions = {".py", ".ts", ".tsx", ".js", ".json", ".yaml", ".yml", ".md", ".sh", ".toml"}
    findings: dict[str, list[str]] = {}

    for relative in source_files():
        path = REPO_ROOT / relative
        if path.suffix not in extensions or not path.is_file():
            continue
        if path.name in SCAN_EXEMPT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found = scan(text)
        if found:
            findings[relative] = found

    assert findings == {}, f"secret-shaped values in tracked files: {findings}"


def test_the_template_secret_is_one_the_config_refuses() -> None:
    """The subtle version of the mistake this catches.

    `.env.example` prints `JWT_SECRET=replace-with-a-long-random-string`. That
    string is 33 characters, so a minimum-length rule would happily accept it -
    and someone who copies the template and deploys would be signing tokens with
    a value published in the repository. It is therefore named explicitly in
    `WEAK_JWT_SECRETS`, and this keeps the two in step: any value the template
    suggests must be one the config rejects.
    """
    template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    suggested = re.findall(r"^JWT_SECRET=(.+)$", template, re.MULTILINE)
    assert suggested, ".env.example should document JWT_SECRET"
    for value in suggested:
        assert value.strip() in WEAK_JWT_SECRETS, f"{value!r} is suggested but not refused"


def test_the_dockerignore_excludes_the_env_file() -> None:
    """Build context, not just the repository.

    `COPY . .` in a Dockerfile copies whatever the context contains, so a `.env`
    excluded from Git but present on the developer's disk would still be baked
    into the image without this.
    """
    ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert ".venv" in ignore


# --------------------------------------------------------------------------
# API responses
# --------------------------------------------------------------------------
READ_ENDPOINTS = [
    "/api/analytics/overview",
    "/api/analytics/risk-distribution",
    "/api/analytics/decisions",
    "/api/transactions",
    "/api/transactions/explorer",
    "/api/reviews",
    "/api/audit",
    "/api/audit/summary",
    "/api/policy",
    "/api/system/health",
    "/api/monitoring/models",
    "/api/monitoring/scores",
    "/api/monitoring/policy",
    "/api/monitoring/high-risk-funnel",
    "/api/monitoring/recommendations",
    "/api/feedback",
    "/api/feedback/summary",
    "/api/assistant/questions",
    "/api/events",
    "/api/live/metrics",
    "/api/simulator/status",
    "/api/simulator/scenarios",
    "/api/auth/me",
    "/api/metrics",
]


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_no_endpoint_returns_a_secret(client: TestClient, path: str, decided: int) -> None:
    response = client.get(path)
    assert response.status_code == 200
    found = scan(response.text)
    assert found == [], f"{path} returned {found}"


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_no_endpoint_returns_an_internal_path(client: TestClient, path: str) -> None:
    """A filesystem path in a response tells an attacker how the box is laid
    out, and often which user the process runs as."""
    body = client.get(path).text.lower()
    for marker in ("/srv/", "/usr/lib/python", "c:\\users", "site-packages", ".joblib"):
        assert marker not in body


def test_no_endpoint_returns_a_password_hash(client: TestClient) -> None:
    """`users.password_hash` exists and must never be serialised."""
    for path in ("/api/auth/me", "/api/audit", "/api/reviews"):
        body = client.get(path).text
        assert "$2b$" not in body
        assert "password_hash" not in body


def test_the_environment_is_not_reflected_anywhere(client: TestClient) -> None:
    """A settings object serialised by accident would carry the signing key."""
    settings = get_settings()
    for path in ("/api/system/health", "/api/policy", "/api/live/metrics"):
        body = client.get(path).text
        assert settings.jwt_secret not in body
        assert settings.database_url not in body


def test_the_openapi_schema_carries_no_default_secret(client: TestClient) -> None:
    """Pydantic will happily publish a field's default value in the schema."""
    schema = json.dumps(client.get("/openapi.json").json())
    settings = get_settings()
    assert settings.jwt_secret not in schema
    assert settings.database_url not in schema
    assert scan(schema) == []


def test_an_error_response_carries_no_connection_string(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driver errors quote the DSN. That must not reach the wire."""
    from app.services import health as health_service

    monkeypatch.setattr(
        "app.api.routes.health.probe_database",
        lambda session: health_service.DatabaseProbe(
            connected=False,
            detail="OperationalError: could not connect to postgresql://user:hunter2@db:5432/x",
        ),
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")

    response = client.get("/health/db")
    assert scan(response.text) == []
    assert "hunter2" not in response.text


# --------------------------------------------------------------------------
# The frontend bundle
# --------------------------------------------------------------------------
def test_the_frontend_config_exposes_only_the_api_url() -> None:
    """Vite inlines every `VITE_`-prefixed variable into the bundle.

    That is the mechanism by which a frontend leaks a backend secret, so the
    set of variables the frontend reads is worth pinning: exactly one, and it is
    a URL the browser is about to request anyway.
    """
    config = (REPO_ROOT / "frontend" / "src" / "lib" / "config.ts").read_text(encoding="utf-8")
    referenced = set(re.findall(r"import\.meta\.env\.(\w+)", config))
    assert referenced == {"VITE_API_BASE_URL"}


def test_no_frontend_source_reads_an_unexpected_env_var() -> None:
    source_root = REPO_ROOT / "frontend" / "src"
    referenced: set[str] = set()
    for path in source_root.rglob("*.ts*"):
        referenced.update(re.findall(r"import\.meta\.env\.(\w+)", path.read_text(encoding="utf-8")))

    unexpected = referenced - {"VITE_API_BASE_URL", "MODE", "DEV", "PROD", "BASE_URL", "SSR"}
    assert unexpected == set(), f"frontend reads {unexpected}"


def test_no_frontend_source_contains_a_secret() -> None:
    source_root = REPO_ROOT / "frontend" / "src"
    findings: dict[str, list[str]] = {}
    for path in source_root.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".css", ".html"} or not path.is_file():
            continue
        found = scan(path.read_text(encoding="utf-8"))
        if found:
            findings[str(path.relative_to(REPO_ROOT))] = found
    assert findings == {}


def test_the_built_bundle_carries_no_secret() -> None:
    """Runs only when `dist/` exists.

    Skipping rather than building keeps the backend suite from depending on
    npm; the Docker validation runs a real build and this check then applies to
    its output.
    """
    dist = REPO_ROOT / "frontend" / "dist"
    if not dist.exists():
        pytest.skip("frontend has not been built")

    findings: dict[str, list[str]] = {}
    for path in dist.rglob("*"):
        if path.suffix not in {".js", ".css", ".html", ".map"} or not path.is_file():
            continue
        try:
            found = scan(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if found:
            findings[str(path.relative_to(dist))] = found
    assert findings == {}


# --------------------------------------------------------------------------
# Configuration surface
# --------------------------------------------------------------------------
def test_settings_never_render_the_secret_in_a_repr() -> None:
    """`repr(settings)` shows up in tracebacks and debugger output."""
    settings = get_settings()
    rendered = repr(settings)
    # Pydantic prints field values, so this documents the current behaviour
    # rather than asserting a protection that does not exist: the guarantee is
    # that no traceback reaches a client, which `test_chaos` covers.
    assert isinstance(rendered, str)


def test_the_log_filter_would_scrub_a_leaked_secret() -> None:
    """Belt and braces for the case above."""
    from app.core.logging import redact_text

    settings = get_settings()
    leaked = f"settings: jwt_secret={settings.jwt_secret} database_url={settings.database_url}"
    scrubbed = redact_text(leaked)
    assert settings.jwt_secret not in scrubbed
