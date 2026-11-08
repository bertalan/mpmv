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
from apps.core.svg import LOGO_COLLECTION_NAME
from apps.core.tests.factories import MediaImageFactory

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
    collection: Collection = Collection.objects.get(name=LOGO_COLLECTION_NAME)
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
    return dict(
        (kind, srcset)
        for srcset, kind in re.findall(r'<source srcset="([^"]+)"[^>]*type="([^"]+)"', html)
    )


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
    hero = next(p for p in pictures if "hero" in p)
    assert 'fetchpriority="high"' in hero
    assert sum('fetchpriority="high"' in p for p in pictures) <= 2  # lead image and hero only


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
    verified_login(client, editor, password)
    licence = MediaImageFactory.create().licence

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
    assert not MediaImage.objects.filter(title="Evil logo").exists()


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
    ],
)
def test_only_existing_images_under_the_image_folders_are_served(
    client: Client, path: str, settings: Any, tmp_path: Any
) -> None:
    (settings.MEDIA_ROOT / "original_images").mkdir(parents=True, exist_ok=True)
    (settings.MEDIA_ROOT / "original_images" / "page.html").write_text("<script>x</script>")

    assert client.get(path).status_code == 404
