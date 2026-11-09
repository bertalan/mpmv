"""CMP.3, owner clarification on #175: the country -> accepted consumer languages map is
staff-editable data with audit, seeded from the official languages of the EU and EEA states.

A country is unblocked when the warnings exist in at least one of its accepted languages, so one
German text unblocks DE, AT and LU at once. The engine stays pure and receives the map; the
services load it (cached per process, dropped on edit).
"""

from collections.abc import Callable

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.tests.factories import ProductFactory
from apps.compliance import services
from apps.compliance.models import CountryWarningLanguages
from apps.compliance.tests.factories import (
    CountryWarningLanguagesFactory,
    GpsrSafetyTextFactory,
)
from apps.core.audit import AuditContext
from apps.core.countries import EU_MEMBER_STATES, OFFICIAL_LANGUAGES
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:compliance", source="admin", ip="127.0.0.1")


def test_the_map_is_seeded_from_the_official_languages_of_the_eu_and_eea() -> None:
    stored = dict(CountryWarningLanguages.objects.values_list("country", "languages"))
    assert set(stored) >= EU_MEMBER_STATES | {"IS", "LI", "NO"}
    assert {country: tuple(codes) for country, codes in stored.items()} == dict(OFFICIAL_LANGUAGES)
    assert set(stored["BE"]) == {"fr", "nl", "de"}
    assert set(stored["LU"]) == {"fr", "de", "lb"}
    assert stored["AT"] == ["de"]
    assert set(stored["IE"]) == {"en", "ga"}
    assert services.country_warning_languages()["BE"] == tuple(stored["BE"])


def test_one_german_text_unblocks_de_at_and_lu_at_once() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="de", warnings="Achtung.")
    for country in ("DE", "AT", "LU", "BE", "LI"):
        assert services.warnings_sale_check(product, country).allowed, country
    assert not services.warnings_sale_check(product, "FR").allowed
    missing = {gap.country for gap in services.warnings_language_gaps(product)}
    assert {"FR", "IT", "ES"} <= missing
    assert not missing & {"DE", "AT", "LU", "BE", "LI"}


def test_staff_add_an_accepted_language_and_it_is_audited() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="en", warnings="Careful.")
    assert not services.warnings_sale_check(product, "FR").allowed

    preview = services.set_country_warning_languages("FR", ["fr", "en"], ctx=CTX, dry_run=True)
    assert [change.op for change in preview.changes] == ["update"]
    assert not services.warnings_sale_check(product, "FR").allowed
    assert not AuditLog.objects.filter(action=services.COUNTRY_LANGUAGES_UPDATED).exists()

    services.set_country_warning_languages("fr", ["FR", "en", "fr"], ctx=CTX)
    assert CountryWarningLanguages.objects.get(country="FR").languages == ["fr", "en"]
    assert services.warnings_sale_check(product, "FR").allowed
    entry = AuditLog.objects.get(action=services.COUNTRY_LANGUAGES_UPDATED)
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["languages"] == ["fr"]
    assert entry.after["languages"] == ["fr", "en"]


def test_staff_can_add_a_country() -> None:
    CountryWarningLanguages.objects.filter(country="CH").delete()
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="en", warnings="Careful.")
    assert services.warnings_sale_check(product, "CH").allowed  # outside the map: not checked
    services.set_country_warning_languages("CH", ["de", "fr", "it"], ctx=CTX)
    assert not services.warnings_sale_check(product, "CH").allowed
    assert AuditLog.objects.filter(action=services.COUNTRY_LANGUAGES_CREATED).exists()


@pytest.mark.parametrize(
    ("country", "languages"),
    [("XX", ["de"]), ("DE", []), ("DE", ["german"]), ("DE", ["d3"])],
)
def test_invalid_values_are_refused_also_on_dry_run(country: str, languages: list[str]) -> None:
    with pytest.raises(ValidationError):
        services.set_country_warning_languages(country, languages, ctx=CTX, dry_run=True)


def test_a_missing_row_falls_back_to_the_official_languages() -> None:
    CountryWarningLanguages.objects.filter(country="AT").delete()
    services.country_languages_changed_now()
    assert services.country_warning_languages()["AT"] == OFFICIAL_LANGUAGES["AT"]


def test_the_map_is_cached_and_dropped_on_edit(settings: object) -> None:
    settings.COMPLIANCE_COUNTRY_LANGUAGES_CACHE_SECONDS = 3600  # type: ignore[attr-defined]
    services.country_languages_changed_now()
    assert services.country_warning_languages()["MT"] == ("mt", "en")
    # A raw write (never done outside tests) is not seen while cached...
    CountryWarningLanguages.objects.filter(country="MT").update(languages=["mt"])
    assert services.country_warning_languages()["MT"] == ("mt", "en")
    # ... the service drops the cache itself.
    services.set_country_warning_languages("MT", ["mt", "en", "it"], ctx=CTX)
    services.country_languages_changed_now()  # tests never commit: on_commit does not fire
    assert services.country_warning_languages()["MT"] == ("mt", "en", "it")


@pytest.fixture
def staff_client(
    client: Client, superuser: User, mfa_login: Callable[[Client, User], Client]
) -> Client:
    return mfa_login(client, superuser)


def test_staff_edit_the_map_in_the_admin_through_the_audited_service(
    staff_client: Client,
) -> None:
    row = CountryWarningLanguages.objects.get(country="IE")
    url = reverse("admin:compliance_countrywarninglanguages_change", args=[row.pk])
    assert staff_client.get(url).status_code == 200
    response = staff_client.post(url, {"languages": "en, ga, fr"})
    assert response.status_code == 302, response.content.decode()[:2000]
    row.refresh_from_db()
    assert row.languages == ["en", "ga", "fr"]
    assert AuditLog.objects.filter(action=services.COUNTRY_LANGUAGES_UPDATED).exists()
    delete = reverse("admin:compliance_countrywarninglanguages_delete", args=[row.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403


def test_a_row_overrides_the_official_languages() -> None:
    CountryWarningLanguagesFactory.create(country="CH", languages=["it"])
    assert services.country_warning_languages()["CH"] == ("it",)
