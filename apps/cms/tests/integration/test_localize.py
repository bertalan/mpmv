"""I18N.3: wagtail-localize for CMS pages, images per locale (D-18, spec 09 §9.2, spec 10).

Done when: "Translated page with sync; a translator replaces one image in one locale only".
Translators work in wagtail-localize's editor; its endpoints are called here the way the editor
calls them. The services are what scripts and migrations (MIG.7) use.
"""

import json
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.models import Locale, Page
from wagtail_localize.models import (
    OverridableSegment,
    StringSegment,
    Translation,
    TranslationSource,
)

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.models import HomePage, StandardPage
from apps.cms.services import (
    SeedReport,
    override_image,
    sync_page_translations,
    translate_page,
)
from apps.cms.tests.factories import StandardPageFactory
from apps.core.models import MediaImage
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


def _image(name: str) -> MediaImage:
    image: MediaImage = MediaImageFactory.create(title=name, file__filename=f"{name}.png")
    return image


def _locale(code: str) -> Locale:
    locale: Locale = Locale.objects.get(language_code=code)
    return locale


def _publish(page: StandardPage) -> StandardPage:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


@pytest.fixture
def editor() -> User:
    user: User = UserFactory.create(superuser=True)
    return user


@pytest.fixture
def workshop(seeded: SeedReport) -> StandardPage:
    """A published EN page with a main image, a figure block and a gallery."""
    page: StandardPage = StandardPageFactory.create(
        title="Workshop",
        slug="workshop",
        image=_image("main-en"),
        image_alt_text="A workbench with tools",
        image_caption="Our workshop",
        body=[
            ("paragraph", "<p>We make things by hand.</p>"),
            (
                "figure",
                {"image": _image("figure-en"), "alt_text": "A lathe", "caption": "The lathe"},
            ),
            (
                "gallery",
                {
                    "images": [
                        {
                            "image": _image("neutral-1"),
                            "alt_text": "Neutral one",
                            "caption": "",
                            "language": "",
                        },
                        {
                            "image": _image("neutral-2"),
                            "alt_text": "Neutral two",
                            "caption": "",
                            "language": "",
                        },
                        {
                            "image": _image("box-de"),
                            "alt_text": "German box",
                            "caption": "",
                            "language": "de",
                        },
                    ]
                },
            ),
        ],
        live=False,
    )
    return _publish(page)


def _translation(page: Page, code: str) -> Translation:
    translation: Translation = Translation.objects.get(
        source__object_id=page.translation_key, target_locale__language_code=code
    )
    return translation


def _in(page: Page, code: str) -> StandardPage:
    translated: StandardPage = page.get_translation(_locale(code)).specific
    return translated


def _block_id(page: StandardPage, block_type: str) -> str:
    block_id: str = next(block.id for block in page.body if block.block_type == block_type)
    return block_id


def _translate_string(client: Client, page: Page, code: str, source: str, text: str) -> None:
    """What the translation editor sends when a translator saves one string."""
    translation = _translation(page, code)
    segment = StringSegment.objects.get(source=translation.source, string__data=source)
    url = reverse("wagtail_localize:edit_string_translation", args=(translation.pk, segment.pk))
    response = client.put(
        url, urlencode({"value": text}), content_type="application/x-www-form-urlencoded"
    )
    assert response.status_code in (200, 201), response.content


def _publish_translation(client: Client, page: Page, code: str) -> None:
    """The editor's "Publish in <language>" button."""
    target = page.get_translation(_locale(code))
    response = client.post(
        reverse("wagtailadmin_pages:edit", args=(target.pk,)), {"action": "publish"}
    )
    assert response.status_code == 302, response.content


# --- Translating and synchronising


def test_a_translation_is_created_under_the_home_page_of_its_language(
    workshop: StandardPage, editor: User
) -> None:
    changes = translate_page(workshop, ["it", "de"], user=editor)

    assert changes.summary() == {"create": 2, "update": 0, "delete": 0}
    for code in ("it", "de"):
        translated = _in(workshop, code)
        assert translated.live
        assert translated.get_parent().specific == HomePage.objects.get(locale=_locale(code))
        assert translated.image == workshop.image  # the source image until overridden
        assert translated.title == "Workshop"  # EN fallback until translated
        assert _translation(workshop, code).enabled


def test_translating_again_or_into_the_source_language_changes_nothing(
    workshop: StandardPage, editor: User
) -> None:
    translate_page(workshop, ["it"], user=editor)
    assert translate_page(workshop, ["it", "en"], user=editor).is_empty


def test_a_dry_run_lists_the_translations_and_writes_nothing(
    workshop: StandardPage, editor: User
) -> None:
    changes = translate_page(workshop, ["it"], user=editor, dry_run=True)

    assert changes.summary()["create"] == 1
    assert not StandardPage.objects.filter(
        translation_key=workshop.translation_key, locale=_locale("it")
    ).exists()
    assert not TranslationSource.objects.filter(object_id=workshop.translation_key).exists()


def test_sync_brings_source_changes_and_keeps_existing_translations(
    workshop: StandardPage, editor: User, client: Client
) -> None:
    translate_page(workshop, ["it"], user=editor)
    verified_login(client, editor)
    _translate_string(client, workshop, "it", "A workbench with tools", "Un banco con attrezzi")
    _publish_translation(client, workshop, "it")

    new_main = _image("main-en-2024")
    workshop.title = "Workshop tour"
    workshop.image = new_main
    workshop = _publish(workshop)
    changes = sync_page_translations(workshop, user=editor)

    translated = _in(workshop, "it")
    assert changes.summary()["update"] == 1
    assert translated.title == "Workshop tour"  # the new EN text, not yet translated
    assert translated.image == new_main  # a synchronised field follows the source
    assert translated.image_alt_text == "Un banco con attrezzi"  # kept: its source is unchanged


def test_a_sync_dry_run_writes_nothing(workshop: StandardPage, editor: User) -> None:
    translate_page(workshop, ["it"], user=editor)
    workshop.title = "Workshop tour"
    workshop = _publish(workshop)

    changes = sync_page_translations(workshop, user=editor, dry_run=True)

    assert changes.summary()["update"] == 1
    assert _in(workshop, "it").title == "Workshop"


# --- Images per locale


def test_a_translator_replaces_one_image_in_one_locale_only(
    workshop: StandardPage, editor: User, client: Client
) -> None:
    translate_page(workshop, ["it", "de"], user=editor)
    italian = _image("main-it")
    translation = _translation(workshop, "it")
    segment = OverridableSegment.objects.get(source=translation.source, context__path="image")
    verified_login(client, editor)

    url = reverse("wagtail_localize:edit_override", args=(translation.pk, segment.pk))
    response = client.put(
        url,
        urlencode({"value": json.dumps(italian.pk)}),
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 201, response.content
    _publish_translation(client, workshop, "it")

    assert _in(workshop, "it").image == italian
    assert _in(workshop, "de").image == workshop.image
    workshop.refresh_from_db()
    assert workshop.image.title == "main-en"

    it_html = client.get(_in(workshop, "it").url).content.decode()
    de_html = client.get(_in(workshop, "de").url).content.decode()
    assert "main-it" in it_html
    assert "main-en" not in it_html
    assert "main-en" in de_html
    assert "main-it" not in de_html


def test_an_image_block_override_survives_a_sync(workshop: StandardPage, editor: User) -> None:
    translate_page(workshop, ["it", "de"], user=editor)
    italian = _image("figure-it")
    path = f"body.{_block_id(workshop, 'figure')}.image"

    changes = override_image(workshop, "it", path, italian, user=editor)
    assert changes.summary()["update"] == 1

    workshop.title = "Workshop tour"
    workshop = _publish(workshop)
    sync_page_translations(workshop, user=editor)

    def figure_image(page: StandardPage) -> str:
        return str(next(b.value["image"].title for b in page.body if b.block_type == "figure"))

    assert figure_image(_in(workshop, "it")) == "figure-it"
    assert figure_image(_in(workshop, "de")) == "figure-en"
    assert figure_image(workshop) == "figure-en"
    assert _in(workshop, "it").title == "Workshop tour"


def test_an_image_override_dry_run_writes_nothing(workshop: StandardPage, editor: User) -> None:
    translate_page(workshop, ["it"], user=editor)

    changes = override_image(workshop, "it", "image", _image("main-it"), user=editor, dry_run=True)

    assert changes.summary()["update"] == 1
    assert _in(workshop, "it").image == workshop.image


def test_overriding_an_unknown_path_or_an_untranslated_language_is_refused(
    workshop: StandardPage, editor: User
) -> None:
    translate_page(workshop, ["it"], user=editor)
    with pytest.raises(LookupError):
        override_image(workshop, "it", "body.no-such-block.image", _image("x"), user=editor)
    with pytest.raises(LookupError):
        override_image(workshop, "fr", "image", _image("y"), user=editor)


# --- Alt text and captions


def test_alt_texts_and_captions_are_translatable_segments(workshop: StandardPage) -> None:
    source, _created = TranslationSource.get_or_create_from_instance(workshop)
    strings = set(
        StringSegment.objects.filter(source=source).values_list("string__data", flat=True)
    )
    assert {
        "A workbench with tools",
        "Our workshop",
        "A lathe",
        "The lathe",
        "Neutral one",
        "Neutral two",
        "German box",
    } <= strings
    paths = set(
        OverridableSegment.objects.filter(source=source).values_list("context__path", flat=True)
    )
    assert "image" in paths
    assert f"body.{_block_id(workshop, 'figure')}.image" in paths


def test_untranslated_alt_texts_fall_back_to_english(
    workshop: StandardPage, editor: User, client: Client
) -> None:
    translate_page(workshop, ["it", "de"], user=editor)
    verified_login(client, editor)
    _translate_string(client, workshop, "it", "A lathe", "Un tornio")
    _translate_string(client, workshop, "it", "The lathe", "Il tornio")
    _publish_translation(client, workshop, "it")

    it_html = client.get(_in(workshop, "it").url).content.decode()
    de_html = client.get(_in(workshop, "de").url).content.decode()
    assert 'alt="Un tornio"' in it_html
    assert "<figcaption>Il tornio</figcaption>" in it_html
    assert 'alt="A lathe"' in de_html
    assert "<figcaption>The lathe</figcaption>" in de_html


# --- Gallery replacement rule (D-18)


def test_the_gallery_shows_dedicated_images_in_their_language_and_neutral_ones_elsewhere(
    workshop: StandardPage, editor: User, client: Client
) -> None:
    translate_page(workshop, ["it", "de"], user=editor)

    def gallery(page: Page) -> str:
        html = client.get(page.url).content.decode()
        start = html.index('class="gallery"')
        return html[start : html.index("</ul>", start)]

    en, it, de = gallery(workshop), gallery(_in(workshop, "it")), gallery(_in(workshop, "de"))
    for neutral in (en, it):
        assert "neutral-1" in neutral
        assert "neutral-2" in neutral
        assert "box-de" not in neutral
    assert "box-de" in de
    assert "neutral-1" not in de
    assert 'alt="German box"' in de


# --- Page validation and rendering


def test_a_main_image_needs_alt_text(seeded: SeedReport) -> None:
    from django.core.exceptions import ValidationError

    page = StandardPageFactory.build(title="Draft", slug="draft", image=_image("main"))
    page.locale = Locale.get_default()
    with pytest.raises(ValidationError) as error:
        page.clean()
    assert "image_alt_text" in error.value.message_dict


def test_the_main_image_renders_with_its_alt_text_and_caption(
    workshop: StandardPage, client: Client
) -> None:
    html = client.get(workshop.url).content.decode()
    assert 'alt="A workbench with tools"' in html
    assert "<figcaption>Our workshop</figcaption>" in html


# --- The translation editor under the strict CSP


def test_the_translation_editor_loads_without_inline_scripts(
    workshop: StandardPage, editor: User, client: Client
) -> None:
    translate_page(workshop, ["it"], user=editor)
    verified_login(client, editor)

    response = client.get(reverse("wagtailadmin_pages:edit", args=(_in(workshop, "it").pk,)))

    assert response.status_code == 200
    html = response.content.decode()
    assert "js-translation-editor" in html
    assert reverse("wagtailimages_chooser:choose") in html  # read from a data attribute
    assert "window.chooserUrls" not in html
    assert "wagtaildocs" not in html


def test_the_submit_translation_form_has_no_inline_script(
    workshop: StandardPage, editor: User, client: Client
) -> None:
    verified_login(client, editor)

    response = client.get(reverse("wagtail_localize:submit_page_translation", args=(workshop.pk,)))

    assert response.status_code == 200
    html = response.content.decode()
    assert 'name="locales"' in html
    assert '<script type="text/javascript">' not in html
