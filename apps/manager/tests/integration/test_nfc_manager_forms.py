"""I18N.10: the Manager staff forms store their free text in NFC; codes, emails, references and
addresses matched byte for byte stay exactly as typed.

Each form is bound with generated text (an "e" and a combining acute accent) in every text field
and its fields are cleaned: the NFC step runs there, before the form's own `clean`.
"""

import json
import secrets
import unicodedata
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django import forms
from django.forms.utils import ErrorDict
from wagtail.admin.rich_text import DraftailRichTextArea

from apps.core.nfc_forms import NFCForm
from apps.manager import event_block_forms as blocks
from apps.manager.amendment_forms import (
    AmendmentForm,
    CancelPackageForm,
    LineChoice,
    ShopCancelForm,
)
from apps.manager.event_forms import EventForm, EventTranslationForm
from apps.manager.event_image_form import EventImageForm
from apps.manager.fitment_forms import (
    AddLevelForm,
    AliasForm,
    NodeForm,
    NodeNameForm,
    ReasonForm,
    SchemeForm,
    TranslatedNameForm,
)
from apps.manager.fitment_shop_forms import ProposalForm
from apps.manager.forms import (
    EndEverySessionForm,
    EndUserSessionsForm,
    ReturnPolicyForm,
    SalesTermsForm,
    ShipmentForm,
    StaffRoleForm,
    StorefrontProfileForm,
    UserLookupForm,
    VendorLogoForm,
)
from apps.manager.media import DocumentUploadForm, ExternalLinkForm, ImageUploadForm
from apps.manager.staff_draft_views import DraftNoteForm
from apps.manager.trust_edit_views import in_force_data
from apps.manager.trust_forms import TrustRulesForm
from apps.orders import amendment_engine as am
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def decomposed(max_length: int | None) -> str:
    """A generated text that ends with an "e" and a combining acute accent."""
    text = f"x{secrets.token_hex(3)}e\u0301"
    return text if max_length is None or max_length >= len(text) else "e\u0301"[:max_length]


def posted(name: str, field: forms.Field) -> tuple[str, str]:
    """The value to post in `field` and the generated text it carries."""
    if isinstance(field, forms.EmailField):
        email = f"{secrets.token_hex(4)}@xe\u0301.example"
        return email, email
    text = decomposed(getattr(field, "max_length", None))
    if name.endswith("url"):  # an address checked as https before it is kept
        address = f"https://www.example.com/{text}"
        return address, address
    if isinstance(field.widget, DraftailRichTextArea):  # Draftail posts a JSON content state
        block = {"key": "a1b2c", "text": text, "type": "unstyled", "depth": 0}
        state = {"blocks": [{**block, "inlineStyleRanges": [], "entityRanges": []}]}
        return json.dumps({**state, "entityMap": {}}), text
    return text, text


def cleaned_fields(build: Callable[[dict[str, str]], NFCForm]) -> tuple[NFCForm, dict[str, str]]:
    """The form bound with generated text in each text field, after its fields are cleaned, and
    the text posted in each field."""
    data: dict[str, str] = {}
    texts: dict[str, str] = {}
    form = build(data)  # the fields of some forms depend on __init__ arguments
    for name, field in form.fields.items():
        if isinstance(field, forms.CharField) and not isinstance(field, forms.URLField):
            data[form.add_prefix(name)], texts[name] = posted(name, field)
    # The same steps as `full_clean`, without the form's `clean` (cross-field rules).
    form._errors = ErrorDict()  # type: ignore[attr-defined]  # unstubbed private attribute
    form.cleaned_data = {}
    form._clean_fields()
    return form, texts


def vendor() -> Vendor:
    return VendorFactory.create(display_name="Shop One")


FORMS: dict[str, tuple[Callable[[dict[str, str]], NFCForm], tuple[str, ...], tuple[str, ...]]] = {
    # name: (build, text fields stored in NFC, fields kept exact)
    "amendment": (
        lambda d: AmendmentForm(d, staff=True),
        ("address_line1", "address_line2", "address_city", "request_text", "reason", "staff_note"),
        ("carrier", "address_country"),
    ),
    "cancel_package": (lambda d: CancelPackageForm(d), ("reason", "staff_note"), ()),
    "shop_cancel": (lambda d: ShopCancelForm(d), ("request_text", "reason"), ()),
    "paragraph": (lambda d: blocks.ParagraphForm(d, vendor=vendor()), ("text",), ()),
    "figure": (lambda d: blocks.FigureForm(d, vendor=vendor()), ("alt_text", "caption"), ()),
    "gallery": (lambda d: blocks.GalleryForm(d, vendor=vendor()), ("alt_text_1", "caption_1"), ()),
    "cta": (lambda d: blocks.CtaForm(d, vendor=vendor()), ("heading", "text", "label"), ()),
    "faq": (lambda d: blocks.FaqForm(d, vendor=vendor()), ("heading", "question_1"), ()),
    "cards": (lambda d: blocks.ProductCardsForm(d, vendor=vendor()), ("heading",), ()),
    "grid": (lambda d: blocks.ProductGridForm(d, vendor=vendor()), ("heading",), ()),
    "event": (
        lambda d: EventForm(d),
        ("title", "summary", "venue_name", "street", "city", "booth"),
        ("timezone", "country", "geo_source", "picked_latitude", "picked_longitude"),
    ),
    "event_translation": (lambda d: EventTranslationForm(d), ("title", "venue_name"), ()),
    "event_image": (
        lambda d: EventImageForm(d),
        ("title", "author", "credit_text"),
        ("permission_ref",),
    ),
    "scheme": (lambda d: SchemeForm(d), (), ("garage_noun_key",)),
    "add_level": (lambda d: AddLevelForm(d), (), ("code",)),
    "node": (lambda d: NodeForm(d), ("name",), ("parent",)),
    "node_name": (lambda d: NodeNameForm(d), ("name",), ()),
    "alias": (lambda d: AliasForm(d), ("alias",), ()),
    "translated_name": (lambda d: TranslatedNameForm(d), ("name",), ()),
    "fitment_reason": (lambda d: ReasonForm(d), ("reason",), ()),
    "proposal": (lambda d: ProposalForm(d, parents=[]), ("name",), ("parent",)),
    "trust_rules": (lambda d: TrustRulesForm(d, current=in_force_data()[0]), ("note",), ()),
    "storefront": (lambda d: StorefrontProfileForm(d), ("tagline", "description"), ()),
    "logo": (lambda d: VendorLogoForm(d), ("logo_alt",), ()),
    "shipment": (lambda d: ShipmentForm(d), (), ("tracking_number",)),
    "staff_role": (lambda d: StaffRoleForm(d), ("reason",), ("email",)),
    "sales_terms": (lambda d: SalesTermsForm(d), ("body", "governing_law"), ()),
    "return_policy": (lambda d: ReturnPolicyForm(d), (), ("returns_page_url", "return_country")),
    "user_lookup": (lambda d: UserLookupForm(d), (), ("email",)),
    "end_sessions": (lambda d: EndUserSessionsForm(d), ("reason",), ()),
    "end_every_session": (lambda d: EndEverySessionForm(d), (), ("confirm",)),
    "image_upload": (lambda d: ImageUploadForm(d), ("title",), ()),
    "document_upload": (lambda d: DocumentUploadForm(d), ("title",), ()),
    "external_link": (lambda d: ExternalLinkForm(d), ("link_text",), ("url",)),
    "draft_note": (lambda d: DraftNoteForm(d, prefix="send"), ("note",), ()),
}


@pytest.mark.parametrize("name", [name for name, spec in FORMS.items() if spec[1]])
def test_the_free_text_is_stored_in_nfc(name: str) -> None:
    build, texts, _exact = FORMS[name]
    form, raw = cleaned_fields(build)
    for field in texts:
        assert field in form.cleaned_data, (field, form.errors.get(field))
        value = str(form.cleaned_data[field])  # rich text: the text inside its markup
        assert unicodedata.normalize("NFC", raw[field]) in value, field
        assert raw[field] not in value, field
        assert unicodedata.is_normalized("NFC", value), field


@pytest.mark.parametrize("name", [name for name, spec in FORMS.items() if spec[2]])
def test_the_exact_fields_are_kept_as_typed(name: str) -> None:
    build, _texts, exact = FORMS[name]
    form, raw = cleaned_fields(build)
    assert set(form.nfc_exact_fields) == set(exact)
    for field in exact:
        value: Any = form.cleaned_data.get(field)
        if isinstance(value, str) and field in raw and not form.errors.get(field):
            assert not unicodedata.is_normalized("NFC", value), field  # no value if refused


@pytest.mark.parametrize(
    ("build", "field"),
    [
        (lambda d: StaffRoleForm(d), "email"),
        (lambda d: EventForm(d), "timezone"),
        (lambda d: EventForm(d), "country"),
        (lambda d: AmendmentForm(d, staff=True), "address_country"),
        (lambda d: ReturnPolicyForm(d), "returns_page_url"),
        (lambda d: EventImageForm(d), "permission_ref"),
        (lambda d: ExternalLinkForm(d), "url"),
        (lambda d: SchemeForm(d), "garage_noun_key"),
        (lambda d: EndEverySessionForm(d), "confirm"),
    ],
)
def test_an_exact_field_keeps_its_decomposed_text(
    build: Callable[[dict[str, str]], NFCForm], field: str
) -> None:
    form, raw = cleaned_fields(build)
    assert form.cleaned_data[field] == raw[field]
    assert not unicodedata.is_normalized("NFC", raw[field])


def test_an_amendment_changes_only_its_text_never_its_amounts() -> None:
    """Order amendment: the reason, the customer's words and the address go to NFC; the
    quantity, the price and the country code reach the changes as they did before I18N.10."""
    line = LineChoice(key="k1", title="Item", quantity=3, unit_price_net=Decimal("10.00"))
    text = {name: decomposed(None) for name in ("reason", "request_text", "address_city")}
    form = AmendmentForm(
        {
            **text,
            "line_k1_quantity": "2",
            "line_k1_price": "9.50",
            "change_address": "on",
            "address_line1": "Via Uno 1",
            "address_country": "it",
            "request_channel": "email",
            "request_date": "2026-11-20",
        },
        lines=[line],
    )
    assert form.is_valid(), form.errors
    assert form.changes() == [
        am.ChangeQuantity("k1", 2, unavailable=False),
        am.ChangePrice("k1", Decimal("9.50")),
        am.ChangeAddress(
            address={
                "line1": "Via Uno 1",
                "city": unicodedata.normalize("NFC", text["address_city"]),
                "country": "IT",
            },
            requested_by_customer=True,
        ),
    ]
    assert form.cleaned_data["reason"] == unicodedata.normalize("NFC", text["reason"])
    request = form.customer_request() or {}
    assert request["text"] == unicodedata.normalize("NFC", text["request_text"])
