"""CAT.20 (3/3): staff upload a brand logo through the SEC.2 pipeline of `core.MediaImage`.

Type, size and pixel caps and the re-encoding without metadata are `MediaImage.clean` and its
field (SEC.2, THM.12); the CMS.8 licence fields are required; the logo goes to the "Logos and
icons" collection (the one that accepts SVG, refused when it holds a script); the change is
audited. Staff only: a
vendor has no way to edit an approved brand.
"""

import io
from collections.abc import Callable

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.catalog import brands
from apps.catalog.models import Brand
from apps.catalog.tests.factories import BrandFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, ImageLicence, MediaImage
from apps.core.tests.factories import MediaImageFactory
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.upload_corpus import Case, assert_safe_image, image_cases, name_cases, png

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
MfaLogin = Callable[[Client, User], Client]
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    b'<rect width="10" height="10"/></svg>'
)
SVG_WITH_SCRIPT = SVG.replace(b"<rect", b"<script>alert(1)</script><rect")


def _file(name: str = "logo.png", data: bytes | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, png() if data is None else data)


def _licence(code: str = "own-work") -> ImageLicence:
    return ImageLicence.objects.get(code=code)


def _set(brand: Brand, **overrides: object) -> None:
    arguments: dict[str, object] = {
        "upload": _file(),
        "alt": "Acme anvil",
        "licence": _licence(),
        "author": "Acme Ltd",
        "ctx": CTX,
    }
    brands.set_brand_logo(brand, **{**arguments, **overrides})  # type: ignore[arg-type]


@pytest.fixture
def brand() -> Brand:
    return BrandFactory.create(name="Acme")


def test_staff_set_a_logo_with_its_licence_in_the_logos_collection(brand: Brand) -> None:
    _set(brand)
    brand.refresh_from_db()
    assert brand.logo is not None
    assert brand.logo_alt == "Acme anvil"
    assert brand.logo.licence == _licence()
    assert brand.logo.author == "Acme Ltd"
    assert brand.logo.collection.name == "Logos and icons"


def test_the_stored_file_is_reencoded_without_metadata(brand: Brand) -> None:
    from PIL import Image

    _set(brand)
    brand.refresh_from_db()
    assert brand.logo is not None
    with brand.logo.file.open("rb") as handle, Image.open(io.BytesIO(handle.read())) as image:
        assert not image.getexif()
        assert image.size == (brand.logo.width, brand.logo.height)


def test_a_plain_svg_logo_is_accepted(brand: Brand) -> None:
    _set(brand, upload=_file("logo.svg", SVG))
    brand.refresh_from_db()
    assert brand.logo is not None
    assert brand.logo.collection.name == "Logos and icons"
    with brand.logo.file.open("rb") as handle:
        assert b"<rect" in handle.read()


def test_an_svg_logo_with_a_script_is_refused(brand: Brand) -> None:
    with pytest.raises(ValidationError):
        _set(brand, upload=_file("logo.svg", SVG_WITH_SCRIPT))
    assert not MediaImage.objects.exists()


def test_a_licence_that_needs_a_source_needs_the_source_url(brand: Brand) -> None:
    with pytest.raises(ValidationError) as refused:
        _set(brand, licence=_licence("CC-BY-4.0"))
    assert "source_url" in refused.value.message_dict
    _set(brand, licence=_licence("CC-BY-4.0"), source_url="https://example.com/acme-logo")
    brand.refresh_from_db()
    assert brand.logo is not None
    assert brand.logo.source_url == "https://example.com/acme-logo"


def test_the_licence_and_the_author_are_required(brand: Brand) -> None:
    for overrides in ({"licence": None}, {"author": ""}, {"author": "  "}):
        with pytest.raises(ValidationError):
            _set(brand, **overrides)
    assert not MediaImage.objects.exists()
    assert Brand.objects.get(pk=brand.pk).logo is None


@pytest.mark.parametrize("alt", ["", "   ", "<b>logo</b>", "x" * 126, "a\x00b"])
def test_the_alt_text_is_short_plain_text(brand: Brand, alt: str) -> None:
    with pytest.raises(ValidationError):
        _set(brand, alt=alt)
    assert not MediaImage.objects.exists()


def test_a_replacement_changes_the_logo_and_the_audit_entry_holds_both_states(
    brand: Brand,
) -> None:
    _set(brand, alt="First")
    first = Brand.objects.get(pk=brand.pk).logo
    _set(brand, alt="Second")
    brand.refresh_from_db()
    assert brand.logo is not None
    assert first is not None
    assert brand.logo.pk != first.pk
    entries = list(AuditLog.objects.filter(action=brands.BRAND_LOGO_CHANGED).order_by("id"))
    assert len(entries) == 2
    assert entries[0].before is not None
    assert entries[0].after is not None
    assert entries[0].before["logo"] is None
    assert entries[0].after["logo_alt"] == "First"
    assert entries[1].before is not None
    assert entries[1].after is not None
    assert entries[1].before["logo_alt"] == "First"
    assert entries[1].after["logo_alt"] == "Second"
    assert entries[1].after["licence"] == "own-work"
    assert all(e.target_key == str(brand.public_id) for e in entries)


def test_only_the_alt_text_can_change_without_a_new_file(brand: Brand) -> None:
    _set(brand, alt="Old")
    kept = Brand.objects.get(pk=brand.pk).logo
    _set(brand, upload=None, alt="Better description")
    brand.refresh_from_db()
    assert brand.logo == kept
    assert brand.logo_alt == "Better description"


def test_without_a_file_or_an_existing_logo_there_is_nothing_to_save(brand: Brand) -> None:
    with pytest.raises(ValidationError):
        _set(brand, upload=None)


def test_a_dry_run_writes_nothing(brand: Brand) -> None:
    changes = brands.set_brand_logo(
        brand, upload=_file(), alt="Acme", licence=_licence(), author="Acme Ltd", ctx=CTX,
        dry_run=True,
    )  # fmt: skip
    assert not changes.is_empty
    assert not MediaImage.objects.exists()
    assert not AuditLog.objects.filter(action=brands.BRAND_LOGO_CHANGED).exists()
    assert Brand.objects.get(pk=brand.pk).logo is None


def test_a_refused_file_keeps_the_current_logo(brand: Brand) -> None:
    _set(brand, alt="Keep me")
    kept = Brand.objects.get(pk=brand.pk).logo
    with pytest.raises(ValidationError):
        _set(brand, upload=_file("logo.png", b"not an image"))
    brand.refresh_from_db()
    assert brand.logo == kept
    assert brand.logo_alt == "Keep me"
    assert MediaImage.objects.count() == 1


def test_a_logo_that_still_needs_its_licence_cannot_be_chosen() -> None:
    unclassified = MediaImageFactory.create(licence=None, author="")
    brand = BrandFactory.build(name="Acme", logo=unclassified, logo_alt="Acme anvil")
    with pytest.raises(ValidationError) as refused:
        brand.full_clean()
    assert "logo" in refused.value.message_dict


def test_a_classified_logo_can_be_chosen() -> None:
    brand = BrandFactory.build(name="Acme", logo=MediaImageFactory.create(), logo_alt="Acme anvil")
    brand.full_clean()


# --- The SEC.2 corpus against this upload path


@pytest.mark.parametrize("case", image_cases() + name_cases(), ids=lambda c: c.id)
def test_the_logo_upload_refuses_or_safely_stores_the_upload_corpus(case: Case) -> None:
    if case.skip:
        pytest.skip(case.skip)
    if case.xfail:
        pytest.xfail(case.xfail)
    brand = BrandFactory.create(name="Acme")
    try:
        _set(brand, upload=SimpleUploadedFile(case.filename, case.data), alt="Logo")
    except ValidationError as refusal:
        errors = set(refusal.message_dict)
        assert errors <= {"logo", "file"}, errors
        assert Brand.objects.get(pk=brand.pk).logo is None, case.why
        assert not MediaImage.objects.exists(), case.why
        assert case.verdict == "refuse", f"{case.id} was refused but should be stored"
        return
    assert case.verdict == "store", f"{case.why}: stored"
    stored = Brand.objects.get(pk=brand.pk).logo
    assert stored is not None
    with stored.file.open("rb") as handle:
        assert_safe_image(stored.file.name, handle.read(), case)


# --- The admin form and who may edit


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def _admin_form(**extra: object) -> dict[str, object]:
    return {
        "name": "Acme",
        "wikidata_id": "",
        "url": "",
        "same_as": "[]",
        "logo_alt": "Acme anvil",
        "translations-TOTAL_FORMS": "0",
        "translations-INITIAL_FORMS": "0",
        "translations-MIN_NUM_FORMS": "0",
        "translations-MAX_NUM_FORMS": "1000",
        **extra,
    }


def test_the_admin_uploads_a_logo_through_the_audited_service(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:catalog_brand_add"),
        _admin_form(
            logo_file=_file(),
            logo_licence=_licence().pk,
            logo_author="Acme Ltd",
        ),
    )
    assert response.status_code == 302
    brand = Brand.objects.get(name="Acme")
    assert brand.logo is not None
    assert brand.logo.licence == _licence()
    assert AuditLog.objects.filter(action=brands.BRAND_LOGO_CHANGED).count() == 1


def test_the_admin_shows_a_refused_upload_as_a_form_error(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:catalog_brand_add"),
        _admin_form(
            logo_file=_file("logo.png", b"not an image"),
            logo_licence=_licence().pk,
            logo_author="Acme Ltd",
        ),
    )
    assert response.status_code == 200
    assert b"errorlist" in response.content
    assert not Brand.objects.filter(name="Acme").exists()
    assert not MediaImage.objects.exists()


def test_the_admin_upload_needs_the_licence_and_the_author(staff_client: Client) -> None:
    response = staff_client.post(reverse("admin:catalog_brand_add"), _admin_form(logo_file=_file()))
    assert response.status_code == 200
    assert not Brand.objects.filter(name="Acme").exists()


def test_a_vendor_cannot_edit_a_brand(client: Client, mfa_login: MfaLogin) -> None:
    approved = BrandFactory.create(name="Acme")
    vendor = VendorFactory.create()
    user = VendorMemberFactory.create(vendor=vendor, role="owner").user
    mfa_login(client, user)
    for url in (
        reverse("admin:catalog_brand_change", args=[approved.pk]),
        reverse("admin:catalog_brand_add"),
    ):
        assert client.get(url).status_code in {302, 403, 404}
        assert client.post(url, _admin_form(logo_file=_file())).status_code in {302, 403, 404}
    assert Brand.objects.get(pk=approved.pk).logo is None


def test_a_staff_user_without_change_permission_cannot_set_a_logo(
    client: Client, mfa_login: MfaLogin
) -> None:
    approved = BrandFactory.create(name="Acme")
    user = UserFactory.create(staff=True, permissions=["catalog.view_brand"])
    mfa_login(client, user)
    client.post(
        reverse("admin:catalog_brand_change", args=[approved.pk]),
        _admin_form(logo_file=_file(), logo_licence=_licence().pk, logo_author="x"),
    )
    assert Brand.objects.get(pk=approved.pk).logo is None
