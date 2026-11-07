"""ARC.8 Done when: flags are manageable from the admin (kill switches behind step-up)."""

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.core import flags
from apps.core.models import AuditLog, FeatureFlag
from apps.core.services import flag_enabled
from apps.core.tests.factories import FeatureFlagFactory
from apps.core.tests.flags import registered

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:core_featureflag_changelist"
FEATURE = flags.FlagSpec("test_feature", "A feature behind a flag.", default=False)


@pytest.fixture
def staff_client(
    client: Client, superuser: Any, mfa_login: Callable[[Client, Any], Client]
) -> Client:
    # `superuser` and `mfa_login` come from the root conftest: core must not import accounts.
    return mfa_login(client, superuser)


@pytest.fixture
def feature() -> Iterator[flags.FlagSpec]:
    with registered(FEATURE) as spec:
        yield spec


@pytest.fixture
def stale_verification(settings: SettingsWrapper) -> None:
    """No second-factor verification counts as fresh: every step-up page asks again."""
    settings.MFA_STEP_UP_MAX_AGE = -1


def change_url(flag: FeatureFlag) -> str:
    return reverse("admin:core_featureflag_change", args=[flag.pk])


def form(flag: FeatureFlag, **overrides: str) -> dict[str, str]:
    return {"state": flag.state, "note": flag.note, **overrides}


def asks_for_step_up(response: Any) -> bool:
    return response.status_code == 302 and urlsplit(response["Location"]).path == reverse(
        "mfa:verify"
    )


def test_the_changelist_lists_the_kill_switches_and_their_effective_value(
    staff_client: Client, settings: SettingsWrapper
) -> None:
    settings.FEATURE_FLAGS = {flags.IMPORT: False}
    response = staff_client.get(reverse(CHANGELIST))
    assert response.status_code == 200
    rows = {flag.name: flag for flag in response.context["cl"].result_list}
    assert {flags.CHECKOUT, flags.IMPORT, flags.PAYMENTS} <= set(rows)
    admin = response.context["cl"].model_admin
    assert admin.effective(rows[flags.CHECKOUT]) == "on"
    assert admin.effective(rows[flags.IMPORT]) == "off"  # this environment's default
    assert admin.environment_default(rows[flags.IMPORT]) == "off"
    assert admin.kill_switch(rows[flags.PAYMENTS]) is True
    content = response.content.decode()
    assert flags.spec_of(flags.CHECKOUT).description in content


def test_staff_pull_a_kill_switch_from_the_admin(staff_client: Client) -> None:
    switch = FeatureFlag.objects.get(name=flags.CHECKOUT)
    response = staff_client.post(
        change_url(switch), form(switch, state=flags.OFF, note="Incident 42")
    )
    assert response.status_code == 302, response.content

    assert flag_enabled(flags.CHECKOUT) is False
    entry = AuditLog.objects.get(action="core.feature_flag.updated")
    assert entry.before is not None
    assert entry.before["state"] == "default"
    assert entry.after is not None
    assert entry.after["state"] == "off"
    assert entry.after["note"] == "Incident 42"
    assert entry.actor_label == "staff:staff@example.com"


def test_the_name_of_a_flag_cannot_be_changed(staff_client: Client) -> None:
    switch = FeatureFlag.objects.get(name=flags.PAYMENTS)
    staff_client.post(change_url(switch), {**form(switch), "name": "renamed"})
    assert FeatureFlag.objects.get(pk=switch.pk).name == flags.PAYMENTS


def test_flags_cannot_be_deleted(staff_client: Client) -> None:
    switch = FeatureFlag.objects.get(name=flags.PAYMENTS)
    delete = reverse("admin:core_featureflag_delete", args=[switch.pk])
    assert staff_client.post(delete, {"post": "yes"}).status_code == 403
    assert FeatureFlag.objects.filter(pk=switch.pk).exists()


def test_staff_add_a_row_for_a_registered_flag_only(
    staff_client: Client, feature: flags.FlagSpec
) -> None:
    add = reverse("admin:core_featureflag_add")
    page = staff_client.get(add)
    assert page.status_code == 200
    choices = {value for value, _ in page.context["adminform"].form.fields["name"].choices}
    assert feature.name in choices
    assert flags.CHECKOUT not in choices  # it already has a row

    refused = staff_client.post(add, {"name": "no_such_flag", "state": flags.ON, "note": ""})
    assert refused.status_code == 200
    assert not FeatureFlag.objects.filter(name="no_such_flag").exists()

    staff_client.post(add, {"name": feature.name, "state": flags.ON, "note": ""})
    assert flag_enabled(feature.name) is True
    assert AuditLog.objects.filter(action="core.feature_flag.created").count() == 1


# --- Step-up (CORE.4) for kill switches


@pytest.mark.usefixtures("stale_verification")
def test_a_kill_switch_asks_for_a_fresh_second_factor(staff_client: Client) -> None:
    switch = FeatureFlag.objects.get(name=flags.CHECKOUT)
    assert asks_for_step_up(staff_client.get(change_url(switch)))
    response = staff_client.post(change_url(switch), form(switch, state=flags.OFF))
    assert asks_for_step_up(response)
    assert flag_enabled(flags.CHECKOUT) is True
    assert asks_for_step_up(staff_client.get(reverse("admin:core_featureflag_add")))


@pytest.mark.usefixtures("stale_verification")
def test_an_ordinary_flag_needs_no_step_up(
    staff_client: Client, feature: flags.FlagSpec
) -> None:
    flag = FeatureFlagFactory.create(name=feature.name)
    assert staff_client.get(change_url(flag)).status_code == 200
    assert staff_client.get(reverse(CHANGELIST)).status_code == 200


def test_anonymous_visitors_cannot_reach_the_flags(client: Client) -> None:
    response = client.get(reverse(CHANGELIST))
    assert response.status_code == 302
    assert reverse("admin:login") in response["Location"]
