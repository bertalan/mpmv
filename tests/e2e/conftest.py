"""E2E suite: a real browser (Playwright) against a live Django server (ADR-0006)."""

import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page

# Playwright's sync API runs an event loop in the test thread; Django's live_server and the
# database still run synchronously, so the async-safety guard must be relaxed for this suite only.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "build" / "e2e" / "screenshots"


@pytest.fixture
def shot(page: Page, request: pytest.FixtureRequest) -> Callable[[str], Path]:
    """Save a named full-page screenshot for the current test; always kept for review."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    test_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)

    def take(step: str) -> Path:
        path = SCREENSHOT_DIR / f"{test_name}--{step}.png"
        page.screenshot(path=str(path), full_page=True)
        return path

    return take
