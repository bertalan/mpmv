"""THM.34 1/2: the palette builder page works as a plain form, behind the theme permission."""

import re
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.themes.models import BrandPalette, ThemeSettings
from apps.themes.services import PALETTE_CONFIGURED, THEME_CONFIGURED
from apps.themes.tests.factories import SiteFactory
from tests.i18n import translated, translated_plural

pytestmark = pytest.mark.django_db

URL = "themes_admin:palette_builder"
MfaLogin = Callable[[Client, Any], Client]


def user_with(*codenames: str) -> Any:
    user = get_user_model().objects.create_user(email=f"{'-'.join(codenames)}@example.com")
    group = Group.objects.create(name="Palette " + ",".join(codenames))
    group.permissions.add(*Permission.objects.filter(codename__in=codenames))
    user.groups.add(group)
    return user


def builder_user() -> Any:
    return user_with("access_admin", "change_themesettings")


def refused(response: Any) -> bool:
    if response.status_code == 403:
        return True
    return bool(
        response.status_code == 302 and response["Location"] == reverse("wagtailadmin_home")
    )


def post(client: Client, **fields: str) -> Any:
    data = {"start": "red", "name": "Mine", "primary": "#C40417", "secondary": "", "band": ""}
    data.update(fields)
    return client.post(reverse(URL), data)


def test_an_anonymous_visitor_cannot_reach_the_builder(client: Client) -> None:
    for response in (client.get(reverse(URL)), client.post(reverse(URL), {"action": "save"})):
        assert response.status_code == 302
        assert "login" in response["Location"]
    assert not BrandPalette.objects.filter(name="Mine").exists()


def test_an_editor_without_the_theme_permission_cannot_use_it(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, user_with("access_admin"))
    assert refused(client.get(reverse(URL)))
    assert refused(post(client, action="save"))
    assert not BrandPalette.objects.filter(name="Mine").exists()


def test_the_page_lists_every_pair_with_its_ratio_and_starts_valid(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    response = client.get(reverse(URL))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'aria-live="polite"' in html
    assert translated("Every pair passes.") in html
    assert html.count("data-pair-result=") >= 14
    assert re.search(r"\d+[.,]\d\d:1", html)
    assert "<script" not in html.split('id="palette-builder"')[1].split("</form>")[0]
    assert 'name="action" value="save"' in html
    assert "disabled" not in html.split('value="save"')[1].split(">")[0]


def test_a_get_and_a_check_write_nothing(client: Client, mfa_login: MfaLogin) -> None:
    mfa_login(client, builder_user())
    client.get(reverse(URL))
    post(client, action="check", primary="#FFE600")
    assert not AuditLog.objects.filter(action=PALETTE_CONFIGURED).exists()
    assert BrandPalette.objects.count() == 3


def test_a_failing_primary_is_refused_on_submit_with_the_pair_and_ratio(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    response = post(client, action="save", primary="#FFE600")
    assert response.status_code == 200
    html = response.content.decode()
    assert "primary (link) on surface" in html
    assert re.search(r'data-pair-result="fail"', html)
    assert translated("Fail") in html
    fails = [
        translated_plural("%(counter)s pair fails.", "%(counter)s pairs fail.", n) % {"counter": n}
        for n in range(1, 10)
    ]
    assert any(text in html for text in fails)
    assert not BrandPalette.objects.filter(name="Mine").exists()
    assert not AuditLog.objects.filter(action=PALETTE_CONFIGURED).exists()


def test_save_is_disabled_with_the_reason_in_text_while_a_pair_fails(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    html = post(client, action="check", primary="#FFE600").content.decode()
    save_button = html.split('value="save"')[1].split(">")[0]
    assert "disabled" in save_button
    assert 'aria-describedby="palette-save-reason"' in save_button
    assert 'id="palette-save-reason"' in html
    reason = "Save is off while a pair fails. Use the suggested shade or pick another colour, "
    assert translated(reason + "then check again.") in html


def test_use_this_puts_the_compliant_shade_in_the_field_without_saving(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    html = post(client, action="check", primary="#FFE600").content.decode()
    offered = re.search(r'name="use" value="primary:(#[0-9A-F]{6})"', html)
    assert offered is not None
    response = post(client, use=f"primary:{offered.group(1)}", primary="#FFE600")
    assert f'value="{offered.group(1)}"' in response.content.decode()
    assert not BrandPalette.objects.filter(name="Mine").exists()


def test_saving_a_passing_palette_goes_through_the_service_and_is_audited(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    response = post(client, action="save", primary="#0B5C7A")
    assert response.status_code == 302
    palette = BrandPalette.objects.get(slug="mine")
    assert palette.primary == "#0B5C7A"
    entry = AuditLog.objects.get(action=PALETTE_CONFIGURED)
    assert entry.after is not None
    assert entry.after["primary"] == "#0B5C7A"
    assert entry.before in (None, {})


def test_saving_for_a_site_applies_it_and_keeps_the_site_theme(
    client: Client, mfa_login: MfaLogin
) -> None:
    site = SiteFactory.create()
    ThemeSettings.objects.create(site=site, theme="base")
    mfa_login(client, builder_user())
    response = post(client, action="save", primary="#0B5C7A", site=str(site.pk))
    assert response.status_code == 302
    setting = ThemeSettings.objects.get(site=site)
    assert setting.palette is not None
    assert setting.palette.slug == "mine"
    assert setting.theme == "base"
    assert AuditLog.objects.filter(action=THEME_CONFIGURED).exists()


def test_a_predefined_palette_cannot_be_overwritten(client: Client, mfa_login: MfaLogin) -> None:
    mfa_login(client, builder_user())
    before = BrandPalette.objects.get(slug="red").roles()
    response = post(client, action="save", name="Red", primary="#0B5C7A")
    assert response.status_code == 200
    assert "predefined palette cannot be overwritten" in response.content.decode()
    assert BrandPalette.objects.get(slug="red").roles() == before


def test_a_value_that_is_not_a_colour_is_an_error_not_a_crash(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    response = post(client, action="save", primary="red")
    assert response.status_code == 200
    assert translated("Use a six digit hex colour such as #C40417.") in response.content.decode()
    assert not BrandPalette.objects.filter(slug="mine").exists()


def test_the_builder_is_in_the_settings_menu_only_for_those_who_may_use_it(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    assert reverse(URL) in client.get(reverse("wagtailadmin_home")).content.decode()
    other = Client()
    mfa_login(other, user_with("access_admin"))
    assert reverse(URL) not in other.get(reverse("wagtailadmin_home")).content.decode()


ACTIONS: list[dict[str, str]] = [
    {"action": "save", "primary": "#0B5C7A"},
    {"action": "save", "primary": "#0B5C7A", "site": "1"},  # the site-palette part of a save
    {"action": "check", "primary": "#FFE600"},
    {"use": "primary:#8A6D00"},
]


@pytest.mark.parametrize("fields", ACTIONS)
def test_every_post_action_is_refused_to_an_anonymous_visitor(
    client: Client, fields: dict[str, str]
) -> None:
    response = post(client, **fields)
    assert response.status_code == 302
    assert "login" in response["Location"]
    assert not BrandPalette.objects.filter(slug="mine").exists()
    assert not AuditLog.objects.filter(action=PALETTE_CONFIGURED).exists()


@pytest.mark.parametrize("fields", ACTIONS)
def test_every_post_action_is_refused_to_an_editor_without_the_permission(
    client: Client, fields: dict[str, str], mfa_login: MfaLogin
) -> None:
    mfa_login(client, user_with("access_admin"))
    assert refused(post(client, **fields))
    assert not BrandPalette.objects.filter(slug="mine").exists()
    assert not AuditLog.objects.filter(action=PALETTE_CONFIGURED).exists()


def test_start_from_is_refused_without_the_permission(client: Client, mfa_login: MfaLogin) -> None:
    assert client.get(reverse(URL), {"start": "blue"}).status_code == 302
    mfa_login(client, user_with("access_admin"))
    assert refused(client.get(reverse(URL), {"start": "blue"}))


def test_a_post_without_a_csrf_token_is_refused(mfa_login: MfaLogin) -> None:
    strict = Client(enforce_csrf_checks=True)
    mfa_login(strict, builder_user())
    response = post(strict, action="save", primary="#0B5C7A")
    assert response.status_code == 403
    assert not BrandPalette.objects.filter(slug="mine").exists()


# --- THM.34 2/2: the live parts (JSON check, generated preview stylesheet, script)


def test_the_page_loads_its_script_and_preview_container_without_inline_code(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    html = client.get(reverse(URL)).content.decode()
    assert re.search(r'<script[^>]+src="[^"]*themes/palette-builder\.js"[^>]+nonce="[^"]+"', html)
    assert 'data-check-url="' in html
    assert 'data-preview-url="' in html
    assert f'data-picker-label="{translated("colour picker")}"' in html
    unreachable = translated("The check could not be reached. Save is off until it works again.")
    assert f'data-check-error="{unreachable}"' in html
    assert 'class="palette-preview"' in html
    assert 'id="palette-preview-css"' in html


def test_the_check_endpoint_reports_pairs_ratios_and_the_shade_to_use(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    response = client.get(
        reverse("themes_admin:palette_check"), {"start": "red", "primary": "#FFE600"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failing"] >= 1
    assert data["valid"] is False
    failed = [row for row in data["rows"] if not row["passes"]]
    assert failed
    assert all(re.fullmatch(r"\d+\.\d\d", row["ratio"]) for row in failed)
    fixes = [row["suggestion"] for row in failed if row["suggestion"]]
    assert fixes
    assert fixes[0]["field"] == "primary"
    assert re.fullmatch(r"#[0-9A-F]{6}", fixes[0]["colour"])
    ok = client.get(reverse("themes_admin:palette_check"), {"start": "red"}).json()
    assert ok["valid"] is True
    assert ok["failing"] == 0


def test_the_check_endpoint_refuses_a_bad_colour_and_users_without_permission(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, user_with("access_admin"))
    assert refused(client.get(reverse("themes_admin:palette_check")))
    assert refused(client.get(reverse("themes_admin:palette_preview")))
    other = Client()
    mfa_login(other, builder_user())
    bad = other.get(reverse("themes_admin:palette_check"), {"primary": "red;}"})
    assert bad.status_code == 400
    assert other.get(reverse("themes_admin:palette_preview"), {"primary": "x"}).status_code == 400


def test_the_preview_stylesheet_is_generated_scoped_and_never_cached(
    client: Client, mfa_login: MfaLogin
) -> None:
    mfa_login(client, builder_user())
    response = client.get(reverse("themes_admin:palette_preview"), {"primary": "#0B5C7A"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/css")
    assert "no-store" in response["Cache-Control"]
    body = response.content.decode()
    assert body.startswith(".palette-preview{")
    assert "--color-accent:#0B5C7A" in body
    assert client.post(reverse("themes_admin:palette_preview")).status_code == 405
