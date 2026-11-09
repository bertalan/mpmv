"""THM.12: modern image formats (spec 14 §14.7).

Done when: pages serve AVIF/WebP with a fallback and correct srcset/sizes; a PNG upload renders
as WebP; a scripted SVG is refused. SVG is for logos and icons only: it is accepted in the
"Logos and icons" collection, checked by `apps.core.svg`, stored re-serialised and served
same-origin as `image/svg+xml` under a restrictive CSP.
"""

import re
import secrets
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.template import Context, Template
from django.test import Client
from wagtail.models import Collection

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.models import StandardPage
from apps.cms.services import SeedReport, seed_site
from apps.cms.tests.factories import StandardPageFactory
from apps.core.models import MediaImage
from apps.core.svg import LOGO_COLLECTION_NAME, sanitise_svg
from apps.core.tests.factories import MediaImageFactory, SvgCollectionFactory

pytestmark = pytest.mark.django_db

SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'
SAFE_SVG = f'<svg {SVG_NS} viewBox="0 0 160 80" width="160" height="80"><!-- c --><rect width="160" height="80" fill="#0a4"/></svg>'  # noqa: E501
SCRIPTED_SVG = f'<svg {SVG_NS} viewBox="0 0 1 1"><script>alert(1)</script></svg>'


@pytest.fixture
def seeded(db: None) -> SeedReport:
    return seed_site()


def _photo(name: str = "lake", width: int = 2000, height: int = 1500) -> MediaImage:
    image: MediaImage = MediaImageFactory.create(
        file__filename=f"{name}.png", file__width=width, file__height=height
    )
    return image


def _logos() -> Collection:
    collection: Collection = Collection.objects.get(svg_policy__isnull=False)
    assert collection.name == LOGO_COLLECTION_NAME
    return collection


def _svg_image(document: str, collection: Collection | None = None) -> MediaImage:
    image = MediaImage(
        title="Logo",
        file=ContentFile(document.encode(), name="logo.svg"),
        collection=collection or _logos(),
        licence=MediaImageFactory.create().licence,
        author="Vendor A",
    )
    return image


def _published(**kwargs: Any) -> StandardPage:
    page: StandardPage = StandardPageFactory.create(live=False, **kwargs)
    page.save_revision().publish()
    page.refresh_from_db()
    return page


# --- Renditions


def test_a_png_upload_renders_as_webp_by_default() -> None:
    image = _photo(width=64, height=48)

    rendition = image.get_rendition("width-32")

    assert rendition.file.name.endswith(".webp")
    with rendition.file.open("rb") as f:
        assert f.read(12)[8:12] == b"WEBP"


def test_an_avif_rendition_is_really_avif() -> None:
    image = _photo(width=64, height=48)

    rendition = image.get_rendition("format-avif|width-32")

    assert rendition.file.name.endswith(".avif")
    with rendition.file.open("rb") as f:
        assert b"ftypavif" in f.read(32)


def _sources(html: str) -> dict[str, str]:
    """{type: srcset} of every <source>, in document order."""
    sources: dict[str, str] = {}
    for tag in re.findall(r"<source [^>]+>", html):
        kind = re.search(r'type="([^"]+)"', tag)
        srcset = re.search(r'srcset="([^"]+)"', tag)
        assert kind is not None
        assert srcset is not None
        sources[kind.group(1)] = srcset.group(1)
    return sources


def test_the_responsive_tag_renders_avif_webp_and_a_jpeg_fallback() -> None:
    image = _photo()
    html = Template(
        '{% load responsive_images %}{% responsive_image image "content" alt="A lake" %}'
    ).render(Context({"image": image}))

    assert html.startswith("<picture>")
    assert list(_sources(html)) == ["image/avif", "image/webp"]
    avif = _sources(html)["image/avif"]
    assert re.fullmatch(r"(\S+\.avif \d+w)(, \S+\.avif \d+w)+", avif)
    assert html.count('sizes="') == 3  # both sources and the fallback <img>
    img = re.search(r"<img [^>]+>", html)
    assert img is not None
    tag = img.group(0)
    assert re.search(r'src="\S+\.jpg"', tag)
    assert re.search(r'srcset="\S+\.jpg \d+w', tag)
    assert re.search(r'width="\d+"', tag)
    assert re.search(r'height="\d+"', tag)
    assert 'alt="A lake"' in tag
    assert 'loading="lazy"' in tag


def test_the_hero_preset_is_eager_with_high_fetch_priority() -> None:
    html = Template(
        '{% load responsive_images %}{% responsive_image image "hero" alt="" priority=True %}'
    ).render(Context({"image": _photo()}))

    tag = re.search(r"<img [^>]+>", html)
    assert tag is not None
    assert 'fetchpriority="high"' in tag.group(0)
    assert 'loading="lazy"' not in tag.group(0)


def test_without_avif_the_picture_keeps_webp_and_jpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.core import images

    monkeypatch.setattr(images, "avif_supported", lambda: False)
    html = Template(
        '{% load responsive_images %}{% responsive_image image "card" alt="" %}'
    ).render(Context({"image": _photo()}))

    assert list(_sources(html)) == ["image/webp"]
    assert ".avif" not in html


def test_a_cms_page_serves_pictures_for_its_lead_image_hero_figure_and_gallery(
    seeded: SeedReport, client: Client
) -> None:
    page = _published(
        slug="formats",
        image=_photo("lead"),
        image_alt_text="Lead",
        body=[
            ("hero", {"heading": "Ride", "image": _photo("hero"), "alt_text": "", "links": []}),
            ("figure", {"image": _photo("figure"), "alt_text": "Hills", "caption": ""}),
            (
                "gallery",
                {"images": [{"image": _photo("gal"), "alt_text": "One", "caption": ""}]},
            ),
        ],
    )

    html = client.get(page.url).content.decode()

    pictures = re.findall(r"<picture>.*?</picture>", html, flags=re.S)
    assert len(pictures) == 4
    for picture in pictures:
        assert list(_sources(picture)) == ["image/avif", "image/webp"]
    lead = pictures[0]
    assert "lead" in lead
    assert 'fetchpriority="high"' in lead
    # Only the LCP image (the lead image) has high priority; the hero below it is lazy.
    assert sum('fetchpriority="high"' in p for p in pictures) == 1
    hero = next(p for p in pictures if "hero" in p)
    assert 'loading="lazy"' in hero


def test_without_a_lead_image_only_the_first_hero_has_high_priority(
    seeded: SeedReport, client: Client
) -> None:
    def hero(name: str) -> tuple[str, dict[str, Any]]:
        return ("hero", {"heading": name, "image": _photo(name), "alt_text": "", "links": []})

    page = _published(slug="heroes", body=[hero("first"), hero("second"), hero("third")])

    html = client.get(page.url).content.decode()

    pictures = re.findall(r"<picture>.*?</picture>", html, flags=re.S)
    assert len(pictures) == 3
    assert [p for p in pictures if 'fetchpriority="high"' in p] == [pictures[0]]
    assert "first" in pictures[0]
    assert all('loading="lazy"' in p for p in pictures[1:])


def test_a_product_card_with_an_image_renders_a_picture() -> None:
    from apps.catalog.services import ProductCard

    card = ProductCard(
        title="Jacket", url="/p/jacket/", price=None, image=_photo("jacket"), image_alt="Jacket"
    )
    html = Template('{% include "catalog/includes/product_card.html" %}').render(
        Context({"card": card, "currency": "EUR"})
    )

    assert list(_sources(html)) == ["image/avif", "image/webp"]
    picture = re.search(r"<picture>.*?</picture>", html, flags=re.S)
    assert picture is not None
    assert 'alt="Jacket"' in picture.group(0)


def test_the_theme_product_tile_and_hero_render_pictures() -> None:
    tile = Template(
        '{% include "components/product_tile.html" with name="Jacket" url="/p/" image=image %}'
    ).render(Context({"image": _photo("tile")}))
    hero = Template('{% include "components/hero.html" with title="Ride" image=image %}').render(
        Context({"image": _photo("hero2")})
    )

    assert list(_sources(tile)) == ["image/avif", "image/webp"]
    assert list(_sources(hero)) == ["image/avif", "image/webp"]
    assert 'fetchpriority="high"' in hero


# --- SVG logos


def test_a_scripted_svg_is_refused() -> None:
    image = _svg_image(SCRIPTED_SVG)

    with pytest.raises(ValidationError) as error:
        image.full_clean()

    assert "file" in error.value.message_dict
    assert "script" in " ".join(error.value.message_dict["file"])


def test_an_svg_outside_the_logos_collection_is_refused() -> None:
    root = Collection.get_first_root_node()
    image = _svg_image(SAFE_SVG, collection=root)

    with pytest.raises(ValidationError) as error:
        image.full_clean()

    assert LOGO_COLLECTION_NAME in " ".join(error.value.message_dict["file"])


def test_an_svg_in_another_collection_named_like_the_logos_one_is_refused() -> None:
    products = Collection.get_first_root_node().add_child(name="Products")
    lookalike = products.add_child(name=LOGO_COLLECTION_NAME)
    image = _svg_image(SAFE_SVG, collection=lookalike)

    with pytest.raises(ValidationError) as error:
        image.full_clean()

    assert "file" in error.value.message_dict


def test_the_logos_collection_still_accepts_svg_after_a_rename() -> None:
    logos = _logos()
    logos.name = "Brand marks"
    logos.save()
    image = _svg_image(SAFE_SVG, collection=logos)

    image.full_clean()  # no error: the collection is identified by its key, not its name


def test_a_sub_collection_of_the_logos_collection_accepts_svg() -> None:
    vendors = _logos().add_child(name="Vendor logos")

    _svg_image(SAFE_SVG, collection=vendors).full_clean()


def test_another_collection_accepts_svg_once_it_has_an_svg_collection_row() -> None:
    marked = SvgCollectionFactory.create().collection

    _svg_image(SAFE_SVG, collection=marked).full_clean()


def test_a_clean_svg_logo_is_stored_re_serialised() -> None:
    image = _svg_image(SAFE_SVG)
    image.full_clean()
    image.save()

    image.refresh_from_db()
    with image.file.open("rb") as f:
        stored = f.read().decode()
    assert "<!--" not in stored
    assert "<rect" in stored
    assert image.is_svg()


def test_an_svg_logo_renders_as_svg_through_the_responsive_tag() -> None:
    image = _svg_image(SAFE_SVG)
    image.full_clean()
    image.save()

    html = Template(
        '{% load responsive_images %}{% responsive_image image "logo" alt="Vendor A" %}'
    ).render(Context({"image": image}))

    assert ".svg" in html
    assert "<source" not in html
    assert 'alt="Vendor A"' in html


def test_the_admin_refuses_a_scripted_svg_upload(client: Client) -> None:
    password = secrets.token_urlsafe(16)
    editor = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    verified_login(client, editor)
    licence = MediaImageFactory.create().licence
    assert licence is not None

    response = client.post(
        "/admin/images/add/",
        {
            "title": "Evil logo",
            "file": ContentFile(SCRIPTED_SVG.encode(), name="evil.svg"),
            "collection": _logos().pk,
            "licence": licence.pk,
            "author": "Someone",
        },
    )

    assert response.status_code == 200  # the form again, with the error
    assert "This SVG is refused" in response.content.decode()
    assert not MediaImage.objects.filter(title="Evil logo").exists()


# --- Every path to storage (lane-a security review of THM.12)

EXPORTED_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<!-- exported -->\n'
    f'<svg {SVG_NS} viewBox="0 0 10 10"><!-- c --><rect width="10" height="10"/></svg>'
)


def _admin(client: Client) -> None:
    password = secrets.token_urlsafe(16)
    editor = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    verified_login(client, editor)


def _upload_step_one(client: Client, document: str, collection: Collection) -> int:
    """Wagtail's multiple upload, step 1: the file is stored as an `UploadedFile` because the
    licence and author are still missing. The id the step-2 form posts to."""
    response = client.post(
        "/admin/images/multiple/add/",
        {
            "files[]": ContentFile(document.encode(), name="logo.svg"),
            "collection": collection.pk,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"], data
    upload_id: int = data["uploaded_file_id"]
    return upload_id


def _upload_step_two(client: Client, upload_id: int, collection: Collection) -> dict[str, Any]:
    licence = MediaImageFactory.create().licence
    assert licence is not None
    prefix = f"uploaded-image-{upload_id}"
    response = client.post(
        f"/admin/images/multiple/create_from_uploaded_image/{upload_id}/",
        {
            f"{prefix}-title": "Multi logo",
            f"{prefix}-collection": collection.pk,
            f"{prefix}-licence": licence.pk,
            f"{prefix}-author": "Vendor A",
        },
    )
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


def test_the_multiple_upload_refuses_an_svg_moved_out_of_the_logos_collection(
    client: Client,
) -> None:
    _admin(client)
    products = Collection.get_first_root_node().add_child(name="Products")
    upload_id = _upload_step_one(client, EXPORTED_SVG, _logos())

    data = _upload_step_two(client, upload_id, products)

    assert not data["success"]
    assert LOGO_COLLECTION_NAME in data["form"]
    assert not MediaImage.objects.filter(title="Multi logo").exists()


def test_the_multiple_upload_stores_the_re_serialised_svg(client: Client) -> None:
    _admin(client)
    upload_id = _upload_step_one(client, EXPORTED_SVG, _logos())

    data = _upload_step_two(client, upload_id, _logos())

    assert data["success"], data
    image = MediaImage.objects.get(title="Multi logo")
    with image.file.open("rb") as f:
        stored = f.read()
    assert stored == sanitise_svg(EXPORTED_SVG.encode())
    assert image.file_size == len(stored)


def _stored_raw_svg(document: str, collection: Collection) -> MediaImage:
    """An image whose file was written to storage before the model saw it, as Wagtail's
    multiple upload does (`file.save(..., save=False)`), bypassing `clean()`."""
    image = MediaImage()
    image.file.save("logo.svg", ContentFile(document.encode()), save=False)
    image._set_image_file_metadata()
    image.width = image.height = 10
    image.title = "Raw logo"
    image.collection = collection
    image.licence = MediaImageFactory.create().licence
    image.author = "Vendor A"
    return image


def test_saving_re_serialises_an_svg_already_in_storage() -> None:
    image = _stored_raw_svg(EXPORTED_SVG, _logos())

    image.save()

    image.refresh_from_db()
    with image.file.open("rb") as f:
        assert f.read() == sanitise_svg(EXPORTED_SVG.encode())


def test_saving_refuses_an_svg_outside_the_logos_collection() -> None:
    products = Collection.get_first_root_node().add_child(name="Products")
    image = _stored_raw_svg(EXPORTED_SVG, products)

    with pytest.raises(ValidationError):
        image.save()

    assert not MediaImage.objects.filter(title="Raw logo").exists()


def test_saving_refuses_a_scripted_svg_already_in_storage() -> None:
    image = _stored_raw_svg(SCRIPTED_SVG, _logos())

    with pytest.raises(ValidationError):
        image.save()


def _bulk_add_to_collection(client: Client, images: list[MediaImage], target: Collection) -> Any:
    ids = "&".join(f"id={image.pk}" for image in images)
    return client.post(
        f"/admin/bulk/core/mediaimage/add_to_collection/?next=/admin/images/&{ids}",
        {"collection": target.pk},
    )


def test_the_bulk_add_to_collection_action_refuses_to_move_an_svg_out_of_logos(
    client: Client,
) -> None:
    _admin(client)
    products = Collection.get_first_root_node().add_child(name="Products")
    logo = _svg_image(SAFE_SVG)
    logo.full_clean()
    logo.save()
    photo = _photo()

    response = _bulk_add_to_collection(client, [logo, photo], products)

    assert response.status_code == 302
    logo.refresh_from_db()
    photo.refresh_from_db()
    assert logo.collection_id == _logos().pk
    assert photo.collection_id != products.pk  # the whole action is refused


def test_the_bulk_add_to_collection_action_still_moves_raster_images(client: Client) -> None:
    _admin(client)
    products = Collection.get_first_root_node().add_child(name="Products")
    photo = _photo()

    _bulk_add_to_collection(client, [photo], products)

    photo.refresh_from_db()
    assert photo.collection_id == products.pk


def test_the_bulk_add_to_collection_action_moves_an_svg_within_svg_collections(
    client: Client,
) -> None:
    _admin(client)
    vendors = _logos().add_child(name="Vendor logos")
    logo = _svg_image(SAFE_SVG)
    logo.full_clean()
    logo.save()

    _bulk_add_to_collection(client, [logo], vendors)

    logo.refresh_from_db()
    assert logo.collection_id == vendors.pk


def _move_collection(client: Client, collection: Collection, parent: Collection) -> Any:
    return client.post(
        f"/admin/collections/{collection.pk}/", {"name": collection.name, "parent": parent.pk}
    )


def test_moving_a_sub_collection_with_svgs_out_of_logos_is_refused(client: Client) -> None:
    _admin(client)
    vendors = _logos().add_child(name="Vendor logos")
    logo = _svg_image(SAFE_SVG, vendors)
    logo.full_clean()
    logo.save()
    root = Collection.get_first_root_node()

    response = _move_collection(client, vendors, root)

    assert response.status_code == 200  # the form again, with the error
    assert "SVG" in response.content.decode()
    vendors.refresh_from_db()
    assert vendors.get_parent() == _logos()
    logo.refresh_from_db()
    assert (
        Collection.objects.ancestor_of(logo.collection, inclusive=True)
        .filter(pk=_logos().pk)
        .exists()
    )


def test_moving_a_collection_with_an_svg_deeper_down_out_of_logos_is_refused(
    client: Client,
) -> None:
    _admin(client)
    vendors = _logos().add_child(name="Vendor logos")
    deeper = vendors.add_child(name="Vendor A")
    logo = _svg_image(SAFE_SVG, deeper)
    logo.full_clean()
    logo.save()

    _move_collection(client, vendors, Collection.get_first_root_node())

    vendors.refresh_from_db()
    assert vendors.get_parent() == _logos()


def test_moving_a_raster_only_sub_collection_out_of_logos_still_works(client: Client) -> None:
    _admin(client)
    vendors = _logos().add_child(name="Vendor logos")
    photo = _photo()
    photo.collection = vendors
    photo.save()
    root = Collection.get_first_root_node()

    response = _move_collection(client, vendors, root)

    assert response.status_code == 302
    vendors.refresh_from_db()
    assert vendors.get_parent() == root


def test_moving_a_sub_collection_with_svgs_within_svg_collections_still_works(
    client: Client,
) -> None:
    _admin(client)
    vendors = _logos().add_child(name="Vendor logos")
    brands = _logos().add_child(name="Brand logos")
    logo = _svg_image(SAFE_SVG, vendors)
    logo.full_clean()
    logo.save()

    response = _move_collection(client, vendors, brands)

    assert response.status_code == 302
    vendors.refresh_from_db()
    assert vendors.get_parent() == brands


def test_moving_the_logos_collection_itself_keeps_its_svgs_allowed(client: Client) -> None:
    _admin(client)
    logo = _svg_image(SAFE_SVG)
    logo.full_clean()
    logo.save()
    brand = Collection.get_first_root_node().add_child(name="Brand")

    response = _move_collection(client, _logos(), brand)

    assert response.status_code == 302
    assert _logos().get_parent() == brand


def test_renaming_a_sub_collection_with_svgs_still_works(client: Client) -> None:
    _admin(client)
    vendors = _logos().add_child(name="Vendor logos")
    logo = _svg_image(SAFE_SVG, vendors)
    logo.full_clean()
    logo.save()

    response = client.post(
        f"/admin/collections/{vendors.pk}/", {"name": "Partner logos", "parent": _logos().pk}
    )

    assert response.status_code == 302
    vendors.refresh_from_db()
    assert vendors.name == "Partner logos"


# --- Serving media


def test_an_svg_is_served_as_an_image_with_a_restrictive_csp(client: Client) -> None:
    image = _svg_image(SAFE_SVG)
    image.full_clean()
    image.save()

    response = client.get(image.file.url)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"
    assert response["Content-Disposition"] == "inline"
    assert response["X-Content-Type-Options"] == "nosniff"
    csp = response["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "sandbox" in csp
    assert "script-src" not in csp


def test_a_raster_rendition_is_served_with_its_type(client: Client) -> None:
    rendition = _photo(width=64, height=48).get_rendition("format-webp|width-32")

    response = client.get(rendition.url)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert "default-src 'none'" in response["Content-Security-Policy"]


@pytest.mark.parametrize(
    "path",
    [
        "/media/../manage.py",
        "/media/original_images/%2e%2e/%2e%2e/manage.py",
        "/media/other/file.png",
        "/media/original_images/page.html",
        "/media/original_images/missing.png",
        # Traversal that stays inside MEDIA_ROOT but leaves the image folders, for example to
        # Wagtail documents in privacy-restricted collections.
        "/media/images/../documents/secret.png",
        "/media/images/%2e%2e/documents/secret.png",
        "/media/images/%2E%2E/documents/secret.png",
        "/media/original_images/..%2fdocuments/secret.png",
        "/media/original_images/%2e%2e%2fdocuments%2fsecret.png",
        "/media/images/./../documents/secret.png",
        "/media/images/sub/../../documents/secret.png",
        "/media/images/..%5cdocuments/secret.png",
        "/media/images//documents/secret.png",
        "/media/documents/secret.png",
        "/media/original_images/a%00b.png",
    ],
)
def test_only_existing_images_under_the_image_folders_are_served(
    client: Client, path: str, settings: Any, tmp_path: Any
) -> None:
    (settings.MEDIA_ROOT / "original_images").mkdir(parents=True, exist_ok=True)
    (settings.MEDIA_ROOT / "original_images" / "page.html").write_text("<script>x</script>")
    (settings.MEDIA_ROOT / "documents").mkdir(parents=True, exist_ok=True)
    (settings.MEDIA_ROOT / "documents" / "secret.png").write_bytes(b"\x89PNG private")

    assert client.get(path).status_code == 404
