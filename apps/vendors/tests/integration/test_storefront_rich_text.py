"""D-34: the storefront description and policies are Wagtail rich text (VEN.10 follow-up).

Saved through the CMS.12 hook, rendered with `|richtext`; images and documents must come from
the vendor's own collection tree, on save and on render.
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import Client
from wagtail.documents import get_document_model
from wagtail.models import Collection

from apps.accounts.models import User
from apps.core.audit import AuditContext
from apps.core.tests.factories import MediaImageFactory
from apps.vendors import engine
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import save_storefront_profile
from apps.vendors.tests.factories import (
    VendorFactory,
    VendorMemberFactory,
    VendorTranslationFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")
FIELDS = ("description", "shipping_policy", "refund_policy", "cancellation_policy")


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE)


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


def _collection(vendor: Vendor) -> Collection:
    root = Collection.get_first_root_node() or Collection.add_root(name="Root")
    return root.add_child(name=engine.vendor_collection_name(str(vendor.public_id)))


def _document(collection: Collection) -> object:
    doc = get_document_model()(title="Terms", collection=collection)
    doc.file.save("terms.txt", ContentFile(b"terms"), save=False)
    doc.save()
    return doc


def _stored(vendor: Vendor, field: str) -> str:
    return str(getattr(VendorTranslation.objects.get(vendor=vendor, language="en"), field))


def _save(vendor: Vendor, owner: User, field: str, html: str) -> None:
    save_storefront_profile(vendor, "en", {field: html}, actor=owner, ctx=CTX)


@pytest.mark.parametrize("field", FIELDS)
def test_formatting_is_kept(vendor: Vendor, owner: User, field: str) -> None:
    link = '<a href="https://example.org/" rel="noopener noreferrer">x</a>'
    html = f"<p><b>Bold</b> <i>it</i></p><ul><li>one</li></ul><p>{link}</p>"
    _save(vendor, owner, field, html)
    assert _stored(vendor, field) == html


@pytest.mark.parametrize(
    ("dirty", "gone"),
    [
        ("<p>a</p><script>alert(1)</script>", "<script"),
        ('<p onclick="x()">a</p>', "onclick"),
        ("<p>a</p><iframe src='https://example.org'></iframe>", "<iframe"),
        ('<p><a href="javascript:alert(1)">a</a></p>', "javascript:"),
    ],
)
def test_dangerous_markup_is_stripped_on_save(
    vendor: Vendor, owner: User, dirty: str, gone: str
) -> None:
    _save(vendor, owner, "refund_policy", dirty)
    assert gone not in _stored(vendor, "refund_policy")


def test_the_length_limit_counts_the_stored_html(vendor: Vendor, owner: User) -> None:
    _save(vendor, owner, "refund_policy", "<p>" + "x" * 4_990 + "</p>")
    with pytest.raises(ValidationError):
        _save(vendor, owner, "refund_policy", "<p>" + "x" * 4_995 + "</p>")


def test_a_foreign_image_or_document_is_refused(vendor: Vendor, owner: User) -> None:
    _collection(vendor)
    other = VendorFactory.create()
    image = MediaImageFactory.create(collection=_collection(other))
    doc = _document(_collection(VendorFactory.create()))
    embed = f'<p>x</p><embed embedtype="image" id="{image.pk}" alt="a" format="fullwidth"/>'
    link = f'<p><a id="{doc.pk}" linktype="document">terms</a></p>'  # type: ignore[attr-defined]
    for html in (embed, link):
        with pytest.raises(ValidationError):
            _save(vendor, owner, "description", html)
    assert not VendorTranslation.objects.filter(vendor=vendor).exists()


def test_a_missing_id_is_refused(vendor: Vendor, owner: User) -> None:
    _collection(vendor)
    with pytest.raises(ValidationError):
        _save(vendor, owner, "description", '<embed embedtype="image" id="999999" alt=""/>')


def test_own_image_and_document_work_also_in_a_subcollection(vendor: Vendor, owner: User) -> None:
    own = _collection(vendor)
    child = own.add_child(name="shop-photos")
    image = MediaImageFactory.create(collection=child)
    doc = _document(own)
    html = (
        f'<embed embedtype="image" id="{image.pk}" alt="a" format="fullwidth"/>'
        f'<p><a id="{doc.pk}" linktype="document">terms</a></p>'  # type: ignore[attr-defined]
    )
    _save(vendor, owner, "description", html)
    assert str(image.pk) in _stored(vendor, "description")


def test_render_rechecks_the_collection_and_hides_the_text(client: Client, vendor: Vendor) -> None:
    own = _collection(vendor)
    image = MediaImageFactory.create(collection=own)
    VendorTranslationFactory.create(
        vendor=vendor,
        language="en",
        status="published",
        description=f'<p>Story</p><embed embedtype="image" id="{image.pk}" alt="a" format="left"/>',
    )
    assert "Story" in client.get(f"/shop/{vendor.slug}/").content.decode()
    root = Collection.get_first_root_node()
    type(image).objects.filter(pk=image.pk).update(collection=root)
    assert "Story" not in client.get(f"/shop/{vendor.slug}/").content.decode()


def test_the_storefront_renders_formatting_and_strips_stored_xss(
    client: Client, vendor: Vendor
) -> None:
    VendorTranslationFactory.create(
        vendor=vendor,
        language="en",
        status="published",
        description="<p>Hi <b>there</b></p><script>boom()</script>",
        shipping_policy='<ul><li>fast</li></ul><p><a href="https://example.org/">x</a></p>',
        refund_policy='<p onclick="x()">r</p><iframe src="https://example.org"></iframe>',
        cancellation_policy='<p><a href="javascript:alert(1)">c</a></p>',
    )
    html = client.get(f"/shop/{vendor.slug}/").content.decode()
    assert "<b>there</b>" in html
    assert "<li>fast</li>" in html
    assert 'href="https://example.org/"' in html
    for bad in ("<script>boom", "onclick", "<iframe", "javascript:", "&lt;p"):
        assert bad not in html


def test_a_legacy_plain_text_policy_renders_as_paragraphs(client: Client, vendor: Vendor) -> None:
    VendorTranslationFactory.create(
        vendor=vendor,
        language="en",
        status="published",
        shipping_policy="First line\nsecond line\n\nNew paragraph",
        description="",
    )
    html = client.get(f"/shop/{vendor.slug}/").content.decode()
    assert "<p>First line<br/>second line</p>" in html
    assert "<p>New paragraph</p>" in html


def test_plain_to_html_is_idempotent() -> None:
    once = engine.plain_to_html("a < b & c\n\nsecond\nline")
    assert once == "<p>a &lt; b &amp; c</p><p>second<br/>line</p>"
    assert engine.plain_to_html(once) == once
    assert engine.plain_to_html("") == ""
    assert engine.plain_to_html("  \n ") == ""


@pytest.mark.django_db(transaction=True)
def test_the_data_migration_converts_plain_text_and_is_idempotent() -> None:
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    before = [("vendors", "0025_vendor_logo")]
    after = [("vendors", "0026_storefront_rich_text")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    OldVendor = old.get_model("vendors", "Vendor")
    OldTranslation = old.get_model("vendors", "VendorTranslation")
    vendor = OldVendor.objects.create(
        legal_name="Vendor A S.r.l.",
        display_name="Vendor A",
        slug="vendor-a",
        country="IT",
        contact_email="vendor-a@example.com",
    )
    from apps.core.translations import source_hash

    texts = {
        "tagline": "Tag",
        "description": "One\ntwo\n\nThree",
        "shipping_policy": "<p>Kept</p><script>x()</script>",
        "refund_policy": "",
        "cancellation_policy": "a < b",
    }
    en = OldTranslation.objects.create(vendor=vendor, language="en", status="published", **texts)
    en.source_hash = source_hash(texts)
    en.save()
    it = OldTranslation.objects.create(
        vendor=vendor, language="it", status="published", source_hash=en.source_hash
    )
    executor = MigrationExecutor(connection)
    executor.migrate(after)
    row = VendorTranslation.objects.get(pk=en.pk)
    assert row.description == "<p>One<br/>two</p><p>Three</p>"
    assert row.shipping_policy == "<p>Kept</p>"
    assert row.cancellation_policy == "<p>a &lt; b</p>"
    assert row.tagline == "Tag"
    assert not VendorTranslation.objects.get(pk=it.pk).is_stale
    # A second run (the data step on converted values) changes nothing.
    import importlib

    from django.apps import apps as live

    module = importlib.import_module("apps.vendors.migrations.0026_storefront_rich_text")
    module.to_rich_text(live, None)
    again = VendorTranslation.objects.get(pk=en.pk)
    assert (again.description, again.shipping_policy, again.cancellation_policy) == (
        row.description,
        row.shipping_policy,
        row.cancellation_policy,
    )
    assert again.source_hash == row.source_hash
    assert not VendorTranslation.objects.get(pk=it.pk).is_stale


def test_the_choosers_list_only_the_members_own_collection_tree(
    vendor: Vendor, owner: User
) -> None:
    from django.test import RequestFactory
    from wagtail.images import get_image_model

    from apps.accounts.tests.factories import UserFactory
    from apps.vendors.wagtail_hooks import scope_image_chooser

    mine = MediaImageFactory.create(collection=_collection(vendor).add_child(name="sub"))
    theirs = MediaImageFactory.create(collection=_collection(VendorFactory.create()))
    loose = MediaImageFactory.create(collection=Collection.get_first_root_node())

    def listed(user: User) -> set[int]:
        request = RequestFactory().get("/")
        request.user = user
        return set(
            scope_image_chooser(get_image_model().objects.all(), request).values_list(
                "pk", flat=True
            )
        )

    assert listed(owner) == {mine.pk}
    staff = UserFactory.create(is_staff=True)
    assert {mine.pk, theirs.pk, loose.pk} <= listed(staff)
