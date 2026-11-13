"""SEO.5: the consent log service."""

import datetime as dt
import uuid

import pytest

from apps.seo import engine, services
from apps.seo.models import ConsentLog

pytestmark = pytest.mark.django_db

ID = uuid.uuid4().hex


def call(**overrides: object) -> object:
    args: dict[str, object] = {
        "consent_id": ID,
        "action": engine.CUSTOM,
        "granted": ["marketing", "statistics", "necessary", "bogus"],
        "banner_version": engine.BANNER_VERSION,
        "provider": "self-hosted",
        "language": "it",
    }
    return services.record_consent(**{**args, **overrides})  # type: ignore[arg-type]


def test_dry_run_returns_the_change_and_writes_nothing() -> None:
    changes = call(dry_run=True)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}  # type: ignore[attr-defined]
    assert not ConsentLog.objects.exists()


def test_it_appends_a_row_with_only_known_categories_in_a_fixed_order() -> None:
    when = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.UTC)
    call(now=when)
    row = ConsentLog.objects.get()
    assert row.granted == ["statistics", "marketing"]
    assert row.decided_at == when
    assert row.language == "it"


def test_an_unknown_action_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown consent action"):
        call(action="maybe")
    assert not ConsentLog.objects.exists()


def test_a_consent_id_must_be_a_uuid() -> None:
    with pytest.raises(ValueError, match="UUID"):
        call(consent_id="not-a-uuid")
