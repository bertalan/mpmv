"""I18N.3: the CMS gallery replacement rule (D-18, spec 09 §9.2, spec 10). Pure function.

A language with at least one image dedicated to it shows only those images; any other language
shows the neutral ones (no language). The rule matches the product gallery rule of CAT.4.
"""

from apps.cms.engine import NEUTRAL, gallery_for_language

NEUTRAL_1 = {"name": "n1", "language": NEUTRAL}
NEUTRAL_2 = {"name": "n2", "language": NEUTRAL}
ITALIAN = {"name": "it1", "language": "it"}
GERMAN_1 = {"name": "de1", "language": "de"}
GERMAN_2 = {"name": "de2", "language": "de"}
GALLERY = [NEUTRAL_1, GERMAN_1, ITALIAN, NEUTRAL_2, GERMAN_2]


def test_a_language_with_dedicated_images_shows_only_those_in_order() -> None:
    assert gallery_for_language(GALLERY, "de") == [GERMAN_1, GERMAN_2]
    assert gallery_for_language(GALLERY, "it") == [ITALIAN]


def test_a_language_without_dedicated_images_shows_the_neutral_ones() -> None:
    assert gallery_for_language(GALLERY, "en") == [NEUTRAL_1, NEUTRAL_2]
    assert gallery_for_language(GALLERY, "fr") == [NEUTRAL_1, NEUTRAL_2]


def test_language_codes_match_whatever_their_case() -> None:
    assert gallery_for_language(GALLERY, "DE") == [GERMAN_1, GERMAN_2]
    assert gallery_for_language([{"language": "IT"}], "it") == [{"language": "IT"}]


def test_a_gallery_of_dedicated_images_only_is_empty_in_other_languages() -> None:
    assert gallery_for_language([ITALIAN], "es") == []
    assert gallery_for_language([], "en") == []
