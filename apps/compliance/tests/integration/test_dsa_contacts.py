"""CMP.5 part 1: DSA points of contact (articles 11, 12) from the platform operator, the public
contact page, the platform terms and ranking pages (DSA art. 14, P2B art. 5) and the readiness
report."""

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.cms.models import LegalPage
from apps.cms.tests.factories import LegalPageFactory
from apps.compliance import services
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.services import configure_platform_operator
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")


def operator(**extra: object) -> object:
    values: dict[str, object] = {
        "open_ended": True,
        "valid_from": timezone.now() - dt.timedelta(days=1),
        "country": "AT",
        "authority_contact_email": "authorities@example.com",
        "authority_contact_languages": ["de", "en"],
        "support_email": "support@example.com",
        "user_contact_url": "https://example.com/contact",
        "user_contact_languages": ["de", "en", "it"],
    }
    values.update(extra)
    return PlatformOperatorFactory.create(**values)


def section(name: str) -> tuple[str, dict[str, str]]:
    return ("legal_section", {"section": name, "heading": name, "text": "<p>Text.</p>"})


def publish(kind: str, *sections: str) -> LegalPage:
    page = LegalPageFactory.create(
        kind=kind, title=f"Page {kind}", slug=f"page-{kind}", body=[section(s) for s in sections]
    )
    page.save_revision().publish()
    return page


# --- Contact data on the operator


def test_contact_fields_are_validated() -> None:
    new = PlatformOperatorFactory.build(
        valid_from=timezone.now(), authority_contact_languages=["Deutsch!"]
    )
    with pytest.raises(ValidationError):
        configure_platform_operator(new, ctx=CTX)


def test_contact_data_changes_through_an_audited_new_version() -> None:
    old = operator()
    new = PlatformOperatorFactory.build(
        valid_from=timezone.now() + dt.timedelta(days=1),
        authority_contact_email="new-authority@example.com",
        authority_contact_languages=["de", "en"],
    )
    configure_platform_operator(new, ctx=CTX)
    entry = AuditLog.objects.filter(target_key=str(new.public_id)).get()
    assert entry.after["authority_contact_email"] == "new-authority@example.com"
    assert entry.after["authority_contact_languages"] == ["de", "en"]
    assert old.pk != new.pk


# --- The public page


def test_contact_page_shows_the_operator_contacts_and_languages() -> None:
    operator()
    response = Client().get(reverse("dsa_contact_points"))
    html = response.content.decode()
    assert response.status_code == 200
    assert "authorities@example.com" in html
    assert "support@example.com" in html
    assert "https://example.com/contact" in html
    assert "Deutsch" in html  # language names, not codes


def test_contact_page_follows_the_current_operator() -> None:
    operator()
    new = PlatformOperatorFactory.build(
        valid_from=timezone.now() + dt.timedelta(seconds=1),
        authority_contact_email="moved@example.com",
        authority_contact_languages=["de", "en"],
        support_email="support@example.com",
    )
    configure_platform_operator(new, ctx=CTX)
    points = services.contact_points(at=timezone.now() + dt.timedelta(days=1))
    assert points.authority_email == "moved@example.com"
    assert services.contact_points().authority_email == "authorities@example.com"


def test_contact_page_is_not_found_without_an_operator() -> None:
    assert Client().get(reverse("dsa_contact_points")).status_code == 404


def test_contact_page_hides_an_empty_optional_url() -> None:
    operator(user_contact_url="")
    assert "contact-form" not in Client().get(reverse("dsa_contact_points")).content.decode()


# --- Readiness


def full_pages(languages: list[str] | None = None) -> None:
    publish("platform_terms", "restrictions", "moderation_policy")
    publish("ranking", "ranking_parameters")


def codes(problems: list[object]) -> list[str]:
    return [p.code for p in problems]  # type: ignore[attr-defined]


def test_readiness_is_clean_with_contacts_and_both_pages() -> None:
    operator()
    full_pages()
    assert services.dsa_p2b_readiness(["en"]) == []


def test_readiness_reports_missing_contacts() -> None:
    operator(authority_contact_email="", authority_contact_languages=[])
    full_pages()
    found = codes(services.dsa_p2b_readiness(["en"]))
    assert "authority_email" in found
    assert "authority_languages" in found


def test_readiness_reports_missing_pages_and_sections() -> None:
    operator()
    publish("platform_terms", "restrictions")
    found = services.dsa_p2b_readiness(["en"])
    assert [p.code for p in found] == ["section_missing", "page_missing"]


def test_readiness_ignores_unpublished_pages_and_empty_sections() -> None:
    operator()
    page = LegalPageFactory.create(
        kind="ranking", title="R", slug="r", body=[section("ranking_parameters")], live=False
    )
    assert page.live is False
    publish("platform_terms", "restrictions", "moderation_policy")
    assert codes(services.dsa_p2b_readiness(["en"])) == ["page_missing"]
    LegalPageFactory.create(
        kind="ranking",
        title="R2",
        slug="r2",
        body=[("legal_section", {"section": "ranking_parameters", "heading": "h", "text": ""})],
    ).save_revision().publish()
    assert codes(services.dsa_p2b_readiness(["en"])) == ["section_missing"]


def test_readiness_command_fails_and_lists_problems() -> None:
    operator()
    with pytest.raises(CommandError, match="platform_terms"):
        call_command("dsa_p2b_readiness", "--language", "en")


def test_readiness_command_passes_when_ready(capsys: pytest.CaptureFixture[str]) -> None:
    operator()
    full_pages()
    call_command("dsa_p2b_readiness", "--language", "en")
    assert "ready" in capsys.readouterr().out
