"""CAT.2: category, taxonomy, term and brand texts reuse the shared TranslationModel (spec 09).

Content is English only at go-live, but every table already holds one row per language, so a new
language is new rows, not a migration.
"""

from typing import Any

import pytest

from apps.catalog.models import (
    BrandTranslation,
    CategoryTranslation,
    TaxonomyTranslation,
    TermTranslation,
)
from apps.catalog.tests.factories import (
    BrandTranslationFactory,
    CategoryTranslationFactory,
    TaxonomyTranslationFactory,
    TermTranslationFactory,
)
from apps.core.checks import check_translation_model
from apps.core.models import TranslationModel

CONTRACTS = [
    (
        CategoryTranslation,
        "category",
        ("name", "description", "seo_title", "seo_description"),
    ),
    (TaxonomyTranslation, "taxonomy", ("name",)),
    (TermTranslation, "term", ("name", "description", "seo_title", "seo_description")),
    (BrandTranslation, "brand", ("description", "seo_title", "seo_description")),
]


@pytest.mark.parametrize(
    ("model", "parent", "texts"), CONTRACTS, ids=[c[0].__name__ for c in CONTRACTS]
)
def test_translation_tables_follow_the_translation_contract(
    model: type[TranslationModel], parent: str, texts: tuple[str, ...]
) -> None:
    assert issubclass(model, TranslationModel)
    assert model.translation_of == parent
    assert model.translated_fields == texts
    assert check_translation_model(model) == []


@pytest.mark.parametrize("model", [c[0] for c in CONTRACTS], ids=lambda m: m.__name__)
def test_the_slug_is_a_url_key_per_language_not_a_translated_text(
    model: Any,
) -> None:
    # Editing the EN slug must not send the other languages back to review.
    assert "slug" not in model.translated_fields
    assert model._meta.get_field("slug").max_length == 200


def test_seo_texts_are_translated_per_language() -> None:
    for model in (CategoryTranslation, TermTranslation, BrandTranslation):
        assert {"seo_title", "seo_description"} <= set(model.translated_fields)


def test_new_translations_start_as_machine_drafts() -> None:
    assert CategoryTranslationFactory.build().status == "machine"
    assert TaxonomyTranslationFactory.build().status == "machine"
    assert TermTranslationFactory.build().status == "machine"
    assert BrandTranslationFactory.build().status == "machine"
