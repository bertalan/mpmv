"""SEC.5 item 8: hostile values through the importers and out through the renderers.

- Shipping configuration import (JSON and CSV): every hostile value in every text cell is
  reported per row or stored; a stored value is exported to CSV without a formula cell.
- Transactional email: CR/LF in the recipient is refused; context values stay inert in the
  subject (one line), the text part and the HTML part (escaped, no linter violation).
- Uploaded image names are stored without a path.
- Rich text: the editor's converter keeps only the allowed features.
- Findings that need more than a small fix are `xfail(strict=True)`, so the day they are fixed
  the suite says so.
"""

import csv
import io
import json
import re
import unicodedata
from typing import Any

import pytest
from wagtail.admin.rich_text.converters.contentstate import ContentstateConverter

from apps.cms.blocks import RICH_TEXT_FEATURES
from apps.core import mail
from apps.core.audit import AuditContext
from apps.core.services import enqueue_email, render_email
from apps.shipping import config_format as fmt
from apps.shipping.config_io import export_config, import_config
from apps.shipping.tests.factories import (
    CarrierFactory,
    PickupOptionFactory,
    ShippingMethodFactory,
    ShippingZoneFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests import hostile_input as hi

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:hostile", source="admin", ip="127.0.0.1")

TEXT_VALUES = (
    *hi.values("html", "template", "sql", "shell", "csv_formulas", "bidi", "zero_width"),
    *hi.values("homoglyphs", "emoji", "non_nfc", "control", "lone_surrogates"),
    hi.at_and_over(255)[1],
)

# --- Shipping configuration import and export -----------------------------------------------


@pytest.fixture
def vendor() -> Vendor:
    CarrierFactory.create(code="main")
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(vendor=vendor, code="home", countries=["FR"])
    ShippingMethodFactory.create(vendor=vendor, zone=zone)
    PickupOptionFactory.create(vendor=vendor, key="shop")
    return vendor


def _with(document: dict[str, Any], value: str) -> dict[str, Any]:
    document = json.loads(json.dumps(document))
    for zone in document["zones"]:
        zone["name"] = value
    for pickup in document["pickup_options"]:
        pickup["address"] = value
    return document


@pytest.mark.parametrize("value", TEXT_VALUES, ids=range(len(TEXT_VALUES)))
def test_a_hostile_import_is_reported_per_row_or_stored(vendor: Vendor, value: str) -> None:
    document = _with(export_config(vendor), value)
    other = VendorFactory.create()

    for parsed in (fmt.parse_json(document), fmt.parse_csv(fmt.dump_csv(document))):
        result = import_config(parsed, vendor=other, ctx=CTX, dry_run=True)
        assert isinstance(result.ok, bool)
        if "\x00" in value or hi.has_lone_surrogate(value):
            assert not result.ok, value


@pytest.mark.parametrize("formula", hi.CSV_FORMULAS)
def test_a_stored_formula_is_exported_as_an_inert_cell(vendor: Vendor, formula: str) -> None:
    document = _with(export_config(vendor), formula)
    other = VendorFactory.create()
    result = import_config(fmt.parse_json(document), vendor=other, ctx=CTX)
    if not result.ok:
        pytest.skip(f"refused on import: {result.to_dict()}")

    text = fmt.dump_csv(export_config(other))

    cells = [cell for row in csv.reader(io.StringIO(text)) for cell in row]
    assert not [c for c in cells if hi.formula_cell(c) and not re.fullmatch(r"-?[\d.]+", c)]


# --- Transactional email --------------------------------------------------------------------


@pytest.mark.parametrize("recipient", [*hi.HEADER_INJECTION[3:], "a@example.com\x00"])
def test_a_recipient_with_a_line_break_is_refused(recipient: str) -> None:
    with pytest.raises(ValueError, match="recipient"):
        enqueue_email(
            recipient=recipient, template_key="tests.order_confirmation", language="en", context={}
        )


@pytest.mark.parametrize("value", hi.STORABLE[:80] + hi.HEADER_INJECTION)
def test_hostile_context_values_stay_inert_in_every_part(value: str) -> None:
    context = {"customer_name": value, "order_number": value, "total": value}

    email = render_email("tests.order_confirmation", "en", context)

    assert hi.header_safe(email.subject)
    assert hi.active_markup(email.html_body) == []
    assert mail.html_violations(email.html_body) == []


def test_a_hostile_context_is_stored_and_sent_as_queued() -> None:
    changes = enqueue_email(
        recipient="customer@example.com",
        template_key="tests.order_confirmation",
        language="en",
        context={"customer_name": hi.HTML[1], "order_number": hi.HEADER_INJECTION[0]},
    )
    assert not changes.is_empty


# --- Uploaded filenames ---------------------------------------------------------------------


@pytest.mark.parametrize("name", hi.FILENAMES)
def test_an_uploaded_image_name_is_stored_without_a_path(name: str) -> None:
    from apps.core.models import MediaImage

    path = MediaImage().get_upload_to(name)

    folder, _, stored = path.rpartition("/")
    assert folder == "original_images"
    assert stored not in ("", ".", "..")
    assert not re.search(r"[\\/\x00-\x1f]", stored)


# --- Rich text ------------------------------------------------------------------------------

ALLOWED_TAGS = {"p", "h2", "h3", "b", "i", "ol", "ul", "li", "a", "br"}


def _editor_round_trip(contentstate: dict[str, Any]) -> str:
    converter = ContentstateConverter(RICH_TEXT_FEATURES)
    return str(converter.to_database_format(json.dumps(contentstate)))


@pytest.mark.parametrize("value", hi.values("html", "word_html", "template", "bidi"))
def test_the_editor_keeps_only_the_allowed_rich_text_features(value: str) -> None:
    contentstate = {
        "blocks": [
            {
                "key": "a",
                "type": block_type,
                "text": value,
                "depth": 0,
                "inlineStyleRanges": [{"offset": 0, "length": 1, "style": "STRIKETHROUGH"}],
                "entityRanges": [{"offset": 0, "length": 1, "key": 0}],
            }
            for block_type in ("unstyled", "header-one", "code-block", "<script>")
        ],
        "entityMap": {
            "0": {"type": "LINK", "mutability": "MUTABLE", "data": {"url": "javascript:alert(1)"}}
        },
    }

    html = _editor_round_trip(contentstate)

    assert hi.active_markup(html) == []
    assert set(re.findall(r"<\s*([a-z0-9]+)", html)) <= ALLOWED_TAGS
    assert not re.search(r"<[^>]*javascript:", html)


# --- Findings (xfail strict: fixed later, the test then turns red as a reminder) -----------


def test_stored_rich_text_is_sanitised_on_output() -> None:
    """F1 (CMS.12): rich text written outside the editor is stored with the allowed features."""
    from wagtail.rich_text import expand_db_html

    from apps.cms.models import BlogIndexPage
    from apps.cms.tests.factories import BlogIndexPageFactory

    page = BlogIndexPageFactory.create(intro=hi.HTML[1] + hi.HTML[6])

    stored = BlogIndexPage.objects.get(pk=page.pk).intro
    assert hi.active_markup(expand_db_html(stored)) == []


def test_the_editor_opens_rich_text_it_did_not_write() -> None:
    """F2 (CMS.12): the editor opens stored HTML it would not write (an <img>)."""
    contentstate = ContentstateConverter(RICH_TEXT_FEATURES).from_database_format(
        "<p>before</p><p><img src=x></p><p>after</p>"
    )

    texts = [block["text"] for block in json.loads(contentstate)["blocks"]]
    assert "before" in texts
    assert "after" in texts


@pytest.mark.xfail(
    strict=True,
    reason="F3: text is stored as typed, not NFC (owner: 'non-NFC forms (store NFC)'); "
    "spec 03 normalises only the SKU match key (NFKC); proposed backlog row I18N.10 in "
    "PR #166 Follow-ups",
)
def test_pasted_text_is_stored_in_nfc(vendor: Vendor) -> None:
    from apps.shipping.models import ShippingZone

    document = _with(export_config(vendor), hi.NON_NFC[0])
    other = VendorFactory.create()
    assert import_config(fmt.parse_json(document), vendor=other, ctx=CTX).ok

    name = ShippingZone.objects.get(vendor=other, code="home").name
    assert name == unicodedata.normalize("NFC", name)
