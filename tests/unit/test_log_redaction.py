"""CORE.15: the secret segment of a secure action link never reaches the logs.

The link token is in the URL path (/account/link/<token>/). Django's request log and gunicorn's
access log both write request paths, so both redact that segment.
"""

import datetime as dt
import logging
import os
import secrets
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from django.conf import settings

from mpmv.gunicorn_logging import RedactingLogger
from mpmv.log_redaction import REDACTED, RedactSecretPaths, redact_secret_paths

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "deploy" / "container" / "entrypoint.sh"


def fake_token() -> str:
    return f"{secrets.token_urlsafe(24)}:{secrets.token_urlsafe(8)}:{secrets.token_urlsafe(32)}"


def test_the_link_segment_is_redacted_wherever_it_appears() -> None:
    token = fake_token()
    line = f'GET /account/link/{token}/ HTTP/1.1 "https://example.com/account/link/{token}/"'
    redacted = redact_secret_paths(line)
    assert token not in redacted
    assert redacted.count(f"/account/link/{REDACTED}/") == 2


def test_the_continue_path_and_other_paths_are_kept() -> None:
    assert redact_secret_paths("POST /account/link/continue/") == "POST /account/link/continue/"
    assert redact_secret_paths("GET /en/shop/link/abc/") == "GET /en/shop/link/abc/"


def test_the_filter_rewrites_the_record_message() -> None:
    token = fake_token()
    record = logging.LogRecord(
        "django.request",
        logging.WARNING,
        __file__,
        1,
        "Method Not Allowed (%s): %s",
        ("POST", f"/account/link/{token}/"),
        None,
    )
    assert RedactSecretPaths().filter(record)
    assert record.getMessage() == f"Method Not Allowed (POST): /account/link/{REDACTED}/"


def test_settings_attach_the_filter_to_the_request_log_and_the_console() -> None:
    config: dict[str, Any] = settings.LOGGING
    [name] = [k for k, v in config["filters"].items() if v["()"].endswith(".RedactSecretPaths")]
    assert name in config["loggers"]["django.request"]["filters"]
    assert name in config["handlers"]["console"]["filters"]


def test_the_gunicorn_access_log_atoms_are_redacted() -> None:
    token = fake_token()
    path = f"/account/link/{token}/"
    environ = {
        "REQUEST_METHOD": "GET",
        "RAW_URI": path,
        "PATH_INFO": path,
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_REFERER": f"https://example.com{path}",
        "REMOTE_ADDR": "192.0.2.1",
    }
    resp = SimpleNamespace(status="200 OK", headers=[], sent=10)
    req = SimpleNamespace(headers=[("REFERER", f"https://example.com{path}")])
    logger = RedactingLogger.__new__(RedactingLogger)  # atoms() needs no configuration

    atoms = logger.atoms(resp, req, environ, dt.timedelta(seconds=1))

    assert token not in repr(atoms)
    assert atoms["r"] == f"GET /account/link/{REDACTED}/ HTTP/1.1"
    assert atoms["U"] == f"/account/link/{REDACTED}/"


def test_the_web_role_runs_gunicorn_with_the_redacting_logger(tmp_path: Path) -> None:
    fake = tmp_path / "gunicorn"
    fake.write_text('#!/bin/sh\necho "gunicorn $*"\n')
    fake.chmod(0o755)
    result = subprocess.run(  # noqa: S603 (fixed script path, test-controlled arguments)
        ["/bin/sh", str(ENTRYPOINT), "web"],
        env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--logger-class mpmv.gunicorn_logging.RedactingLogger" in result.stdout
    assert "--access-logfile -" in result.stdout
