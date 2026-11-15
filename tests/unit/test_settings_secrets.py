"""No settings module falls back to a hard-coded secret on STAGE or LIVE (SEC.5 item 7).

The production settings are imported in a fresh interpreter with a clean environment, so the
process-wide test settings (which set placeholder keys) cannot leak in. A required secret that is
missing or empty stops the import with ImproperlyConfigured; an optional one (a provider key, a
webhook secret) stays empty, which turns its feature off or makes a system check fail. The dev
and test placeholder keys say "insecure" and the production settings refuse such a key.
"""

import ast
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "mpmv" / "settings"
PRODUCTION = ["mpmv.settings.live", "mpmv.settings.stage"]
REQUIRED = ["DJANGO_SECRET_KEY", "DATABASE_URL"]
# Optional secrets: empty unless the environment provides them.
OPTIONAL = {
    "MPMV_EMAIL_WEBHOOK_SECRET": "EMAIL_WEBHOOK_SECRET",
    "TURNSTILE_SECRET_KEY": "TURNSTILE_SECRET_KEY",
    "MPMV_GEOCODING_LOCATIONIQ_KEY": "GEOCODING_LOCATIONIQ_KEY",
    "MPMV_GEOCODING_OPENCAGE_KEY": "GEOCODING_OPENCAGE_KEY",
}
SECRET_NAME = re.compile(r"SECRET|PASSWORD|TOKEN|CREDENTIAL|PRIVATE|(^|_)KEYS?$")
# Environment variables that name a secret (the settings read them through env_* helpers).
SECRET_ENV = re.compile(r"SECRET|PASSWORD|TOKEN|CREDENTIAL|PRIVATE|_KEYS?$|DATABASE_URL")
URL_WITH_PASSWORD = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@\"']+:[^/\s@\"']+@")

PROBE = """
import importlib, json, sys
try:
    module = importlib.import_module(sys.argv[1])
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
    raise SystemExit(0)
values = {name: value for name, value in vars(module).items() if name.isupper()}
values["DATABASE_PASSWORD"] = values["DATABASES"]["default"].get("PASSWORD", "")
del values["DATABASES"]
print(json.dumps({"settings": values}, default=repr))
"""


def import_settings(module: str, env: dict[str, str]) -> dict[str, Any]:
    clean = {name: os.environ[name] for name in ("PATH", "HOME", "LANG") if name in os.environ}
    result = subprocess.run(  # noqa: S603 (this interpreter, a fixed probe, test-set environment)
        [sys.executable, "-c", PROBE, module],
        cwd=ROOT,
        env={**clean, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert isinstance(data, dict)
    return data


def required_env() -> dict[str, str]:
    password = secrets.token_urlsafe(16)
    return {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
        "DATABASE_URL": f"postgres://mpmv:{password}@db.example.internal:5432/mpmv",
        "DJANGO_ALLOWED_HOSTS": "shop.example.com",
    }


@pytest.mark.parametrize("module", PRODUCTION)
@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("value", [None, "", "  "], ids=["unset", "empty", "blank"])
def test_a_missing_required_secret_stops_the_production_settings(
    module: str, missing: str, value: str | None
) -> None:
    env = required_env()
    if value is None:
        del env[missing]
    else:
        env[missing] = value
    result = import_settings(module, env)
    assert result.get("error") == "ImproperlyConfigured", result
    assert missing in result["message"]


@pytest.mark.parametrize("module", PRODUCTION)
@pytest.mark.parametrize(
    "placeholder", ["dev-only-insecure-key", "test-only-insecure-key", "django-insecure-abc"]
)
def test_production_settings_refuse_a_dev_or_test_placeholder_key(
    module: str, placeholder: str
) -> None:
    env = {**required_env(), "DJANGO_SECRET_KEY": placeholder}
    result = import_settings(module, env)
    assert result.get("error") == "ImproperlyConfigured", result
    assert "DJANGO_SECRET_KEY" in result["message"]
    assert placeholder not in result["message"]  # never echo a key, even a placeholder


@pytest.mark.parametrize("module", PRODUCTION)
def test_without_optional_secrets_every_secret_setting_is_empty(module: str) -> None:
    env = required_env()
    settings = import_settings(module, env)["settings"]
    assert settings["SECRET_KEY"] == env["DJANGO_SECRET_KEY"]
    assert settings["DATABASE_PASSWORD"] == env["DATABASE_URL"].split(":")[2].split("@")[0]
    assert settings["FIELD_ENCRYPTION_KEYS"] == []  # system check core.E301 then fails
    for setting in OPTIONAL.values():
        assert settings[setting] == "", setting
    for name, value in settings.items():
        if not SECRET_NAME.search(name) or name in {"SECRET_KEY", "DATABASE_PASSWORD"}:
            continue
        if isinstance(value, str) or (isinstance(value, list) and all(map(is_str, value))):
            assert not value, f"{name} has a default value"


def is_str(value: object) -> bool:
    return isinstance(value, str)


@pytest.mark.parametrize("module", PRODUCTION)
def test_every_optional_secret_comes_from_its_environment_variable(module: str) -> None:
    sentinels = {variable: secrets.token_urlsafe(24) for variable in OPTIONAL}
    keys = f"live:{secrets.token_urlsafe(32)}"
    env = {**required_env(), **sentinels, "MPMV_FIELD_ENCRYPTION_KEYS": keys}
    settings = import_settings(module, env)["settings"]
    for variable, setting in OPTIONAL.items():
        assert settings[setting] == sentinels[variable], setting
    assert settings["FIELD_ENCRYPTION_KEYS"] == [keys]


# --- Source scan: no credential-like literal default in any settings module ---------------------


def settings_sources() -> list[Path]:
    return sorted(SETTINGS.glob("*.py"))


def literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def secret_defaults(tree: ast.AST) -> list[tuple[int, str, str]]:
    """(line, name, default) for every non-empty literal given to a secret-like name."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = literal(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name) and SECRET_NAME.search(target.id) and value:
                    found.append((node.lineno, target.id, value))
        elif isinstance(node, ast.Call) and len(node.args) >= 2:
            name, default = literal(node.args[0]), literal(node.args[1])
            if name and default and SECRET_ENV.search(name):
                found.append((node.lineno, name, default))
    return sorted(found)


@pytest.mark.parametrize("path", settings_sources(), ids=lambda path: path.name)
def test_settings_contain_no_credential_like_literal_default(path: Path) -> None:
    source = path.read_text()
    assert not URL_WITH_PASSWORD.search(source), f"{path.name}: a URL with a password"
    for line, name, default in secret_defaults(ast.parse(source)):
        if name == "DATABASE_URL":
            # The local database of dev and test: trust authentication, no password in the URL.
            assert path.name in {"dev.py", "test.py"}, f"{path.name}:{line} {name}"
            assert "127.0.0.1" in default, f"{path.name}:{line} {name}"
            continue
        # Only the dev and test settings may set a placeholder, and it must say so.
        assert path.name in {"dev.py", "test.py"}, f"{path.name}:{line} {name} has a default"
        assert "insecure" in default, f"{path.name}:{line} {name}: not marked insecure"


def test_the_scan_catches_a_hard_coded_secret() -> None:
    tree = ast.parse(
        'X = env_str("STRIPE_SECRET_KEY", "sk_live_value")\n'
        'TURNSTILE_SECRET_KEY = "a-literal"\n'
        'Y = os.environ.get("MPMV_API_TOKEN", "abc")\n'
        'Z = env_str("TURNSTILE_SECRET_KEY", "")\n'
    )
    assert [(line, name) for line, name, _ in secret_defaults(tree)] == [
        (1, "STRIPE_SECRET_KEY"),
        (2, "TURNSTILE_SECRET_KEY"),
        (3, "MPMV_API_TOKEN"),
    ]
    assert URL_WITH_PASSWORD.search("postgres://user:pw@host/db")
    assert not URL_WITH_PASSWORD.search("postgres://mpmv@127.0.0.1:55432/mpmv")


@pytest.mark.parametrize("module", ["base.py", "live.py", "stage.py", "env.py"])
def test_production_modules_never_import_the_dev_or_test_settings(module: str) -> None:
    tree = ast.parse((SETTINGS / module).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            name = node.module or ""
            assert name.split(".")[-1] not in {"dev", "test"}, f"{module} imports {name}"
