"""I18N.10 follow-up (#757): strings that production showed in English in every language.

The vendor claim pages, the `full_name` label, the image licence and author labels with the
seeded "Own work" licence, the theme settings labels and every order, sub-order and amendment
state label. Each renders in each configured language exactly as the catalog says.
"""

import pytest
from django.db.models import TextChoices
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import translation

from apps.accounts.models import User
from apps.core.i18n import configured_languages
from apps.core.models import ImageLicence, MediaImage
from apps.orders.models import AmendmentState, OrderState, StaffDraftState, VendorOrderState
from apps.themes.models import ThemeSettings
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import translated

pytestmark = pytest.mark.django_db

LANGS = configured_languages()


@pytest.mark.parametrize("lang", LANGS)
def test_the_claim_identity_page_speaks_the_active_language(lang: str) -> None:
    with translation.override(lang):
        html = render_to_string(
            "vendors/claim/identity.html",
            {"vendor": VendorFactory.build()},
            request=RequestFactory().get("/"),
        )
    for msgid in ("VAT number or TIN", "Confirm"):
        assert translated(msgid, lang) in html


@pytest.mark.parametrize("lang", LANGS)
def test_the_claim_account_page_speaks_the_active_language(lang: str) -> None:
    context = {
        "vendor": VendorFactory.build(),
        "email": "a@example.com",
        "qr_svg": "",
        "secret": "x",
    }
    with translation.override(lang):
        html = render_to_string(
            "vendors/claim/account.html", context, request=RequestFactory().get("/")
        )
    for msgid in ("Full name", "Password again", "Submit the claim", "Authenticator app"):
        assert translated(msgid, lang) in html


@pytest.mark.parametrize("lang", LANGS)
def test_model_field_labels_are_translated(lang: str) -> None:
    with translation.override(lang):
        labels = {
            "Full name": str(User._meta.get_field("full_name").verbose_name),
            "Licence": str(MediaImage._meta.get_field("licence").verbose_name),
            "Author": str(MediaImage._meta.get_field("author").verbose_name),
            "Site": str(ThemeSettings._meta.get_field("site").verbose_name),
            "Theme": str(ThemeSettings._meta.get_field("theme").verbose_name),
        }
    for msgid, label in labels.items():
        assert label == translated(msgid, lang)


@pytest.mark.parametrize("lang", LANGS)
def test_the_seeded_own_work_licence_option_is_translated(lang: str) -> None:
    own = ImageLicence(code="own-work", name="Own work")
    other = ImageLicence(code="cc-by", name="CC BY 4.0")
    with translation.override(lang):
        assert str(own) == translated("Own work", lang)
        assert str(other) == "CC BY 4.0"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("choices", [OrderState, VendorOrderState, AmendmentState, StaffDraftState])
def test_every_state_label_is_translated_and_stored_values_are_unchanged(
    lang: str, choices: type[TextChoices]
) -> None:
    for member in choices:
        with translation.override("en"):
            english = str(member.label)
        with translation.override(lang):
            assert str(member.label) == translated(english, lang)
            if lang != "en":
                assert str(member.label) != english
        assert member.value == member.name.lower()


def test_the_pending_and_accepted_labels_differ_from_english_in_the_catalog_languages() -> None:
    for lang in LANGS:
        if lang == "en":
            continue
        assert translated("Pending", lang) != "Pending"
        assert translated("Accepted", lang) != "Accepted"
