"""CMS.1: sample texts for the seed pages, with the primary language as fallback."""

from django.conf import settings
from django.utils.text import slugify

from apps.cms.seed import SEED_TEXTS, seed_text


def test_every_configured_language_has_sample_texts() -> None:
    assert {code for code, _name in settings.LANGUAGES} <= set(SEED_TEXTS)


def test_the_five_planned_languages_have_sample_texts() -> None:
    # The five planned languages (I18N.1). The seed covers them whichever of them
    # settings.LANGUAGES lists today, so adding FR and ES to settings needs no seed change.
    assert {"en", "it", "de", "fr", "es"} <= set(SEED_TEXTS)


def test_a_language_without_texts_gets_the_primary_language_texts() -> None:
    assert "pt" not in SEED_TEXTS  # a hypothetical sixth language
    assert seed_text("pt") == SEED_TEXTS[settings.LANGUAGE_CODE]


def test_static_page_slugs_are_translated_and_url_safe() -> None:
    slugs = [texts.about.slug for texts in SEED_TEXTS.values()]
    assert len(set(slugs)) == len(slugs)  # e.g. /about/, /it/chi-siamo/, /de/ueber-uns/
    assert all(slug == slugify(slug) for slug in slugs)
