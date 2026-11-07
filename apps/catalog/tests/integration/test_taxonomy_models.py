"""CAT.2: category tree, configurable taxonomies and terms, brands, their translations, and how
products link to them (spec 03 §3.2)."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.catalog import services
from apps.catalog.models import Brand, Category, CategoryTranslation, Term
from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)

pytestmark = pytest.mark.django_db


# --- Category tree


def test_categories_form_a_tree_in_path_order() -> None:
    bags = CategoryFactory.create()
    backpacks = CategoryFactory.create(parent=bags)
    hats = CategoryFactory.create()
    bags.refresh_from_db()
    assert list(Category.objects.all()) == [bags, backpacks, hats]
    assert backpacks.get_parent() == bags
    assert list(bags.get_children()) == [backpacks]
    assert backpacks.depth == 2


def test_category_texts_resolve_with_the_explicit_english_fallback() -> None:
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, language="en", name="Bags", slug="bags", status="published"
    )
    CategoryTranslationFactory.create(
        category=category, language="it", name="Borse", slug="borse", status="machine"
    )
    italian = category.translation("it")
    assert italian.is_fallback is True  # the IT draft is not published yet
    assert italian.translation is not None
    assert italian.translation.name == "Bags"
    assert str(category) == "Bags"


def test_changing_the_english_name_sends_published_translations_back_to_review() -> None:
    category = CategoryFactory.create()
    english = CategoryTranslationFactory.create(category=category, language="en", name="Bags")
    italian = CategoryTranslationFactory.create(
        category=category, language="it", name="Borse", status="published"
    )
    english.name = "Bags and backpacks"
    english.save()
    italian.refresh_from_db()
    assert italian.status == "machine"
    assert italian.is_stale is True


def test_sibling_categories_cannot_share_a_slug_in_one_language() -> None:
    parent = CategoryFactory.create()
    first = CategoryFactory.create(parent=parent)
    second = CategoryFactory.create(parent=parent)
    CategoryTranslationFactory.create(category=first, language="en", slug="leather")
    duplicate = CategoryTranslationFactory.build(category=second, language="en", slug="leather")
    with pytest.raises(ValidationError) as excinfo:
        duplicate.full_clean()
    assert excinfo.value.message_dict["slug"] == [
        "Another category at this level already uses this slug in this language."
    ]


def test_the_same_slug_is_allowed_under_another_parent_or_in_another_language() -> None:
    first = CategoryFactory.create(parent=CategoryFactory.create())
    cousin = CategoryFactory.create(parent=CategoryFactory.create())
    CategoryTranslationFactory.create(category=first, language="en", slug="leather")
    CategoryTranslationFactory.build(category=cousin, language="en", slug="leather").full_clean()
    sibling = CategoryFactory.create(parent=first.get_parent())
    CategoryTranslationFactory.build(category=sibling, language="it", slug="leather").full_clean()


def test_root_categories_are_siblings_too() -> None:
    CategoryTranslationFactory.create(category=CategoryFactory.create(), slug="bags")
    duplicate = CategoryTranslationFactory.build(category=CategoryFactory.create(), slug="bags")
    with pytest.raises(ValidationError):
        duplicate.full_clean()


def test_slugs_are_lower_case_url_segments() -> None:
    translation = CategoryTranslationFactory.build(
        category=CategoryFactory.create(), slug="Leather_Bags"
    )
    with pytest.raises(ValidationError) as excinfo:
        translation.full_clean()
    assert "slug" in excinfo.value.message_dict


def test_one_translation_per_category_and_language() -> None:
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(category=category, language="en")
    with pytest.raises(IntegrityError), transaction.atomic():
        CategoryTranslationFactory.create(category=category, language="en")


def test_deleting_a_category_deletes_its_translations_and_subtree() -> None:
    parent = CategoryFactory.create(languages=["en"])
    CategoryFactory.create(parent=parent, languages=["en"])
    parent.delete()
    assert Category.objects.count() == 0
    assert CategoryTranslation.objects.count() == 0


# --- Configurable taxonomies


def test_a_taxonomy_is_configured_as_data() -> None:
    taxonomy = TaxonomyFactory.create(
        code="compatibility",
        hierarchical=True,
        filterable=True,
        has_landing=True,
        term_attributes=[{"code": "model_year", "kind": "number"}],
    )
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", name="Compatibility", slug="compatibility"
    )
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="it", name="Compatibilità", slug="compatibilita"
    )
    taxonomy.full_clean()
    assert sorted(taxonomy.translations.values_list("slug", flat=True)) == [
        "compatibilita",
        "compatibility",
    ]


def test_taxonomy_codes_are_unique() -> None:
    TaxonomyFactory.create(code="occasion")
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxonomyFactory.create(code="occasion")


def test_taxonomy_slugs_are_unique_per_language() -> None:
    TaxonomyTranslationFactory.create(language="en", slug="occasion")
    TaxonomyTranslationFactory.create(language="it", slug="occasion")
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxonomyTranslationFactory.create(language="en", slug="occasion")


def test_a_malformed_term_schema_is_rejected() -> None:
    taxonomy = TaxonomyFactory.build(term_attributes=[{"code": "x", "kind": "date"}])
    with pytest.raises(ValidationError) as excinfo:
        taxonomy.full_clean()
    assert "term_attributes" in excinfo.value.message_dict


def test_a_taxonomy_with_child_terms_cannot_become_flat() -> None:
    taxonomy = TaxonomyFactory.create(hierarchical=True)
    TermFactory.create(parent=TermFactory.create(taxonomy=taxonomy))
    taxonomy.hierarchical = False
    with pytest.raises(ValidationError) as excinfo:
        taxonomy.full_clean()
    assert excinfo.value.message_dict["hierarchical"] == [
        "Some terms of this taxonomy have children: move them to the top level first."
    ]


# --- Terms


def test_terms_form_one_tree_per_taxonomy() -> None:
    taxonomy = TaxonomyFactory.create(hierarchical=True)
    maker = TermFactory.create(taxonomy=taxonomy)
    model = TermFactory.create(parent=maker)
    assert model.taxonomy == taxonomy
    assert model.get_parent() == maker
    other = TermFactory.create()
    assert other.taxonomy != taxonomy
    assert list(Term.objects.filter(taxonomy=taxonomy)) == [maker, model]


def test_a_flat_taxonomy_refuses_child_terms() -> None:
    root = TermFactory.create(taxonomy=TaxonomyFactory.create(hierarchical=False))
    with pytest.raises(ValidationError, match="This taxonomy is flat"):
        TermFactory.create(parent=root)


def test_a_child_term_must_belong_to_the_parent_taxonomy() -> None:
    root = TermFactory.create()
    with pytest.raises(ValidationError, match="same taxonomy"):
        TermFactory.create(parent=root, taxonomy=TaxonomyFactory.create())


def test_term_attributes_are_validated_against_the_taxonomy_schema() -> None:
    taxonomy = TaxonomyFactory.create(
        term_attributes=[{"code": "model_year", "kind": "number", "required": True}]
    )
    TermFactory.create(taxonomy=taxonomy, attributes={"model_year": 2020}).full_clean()
    term = TermFactory.create(taxonomy=taxonomy, attributes={"model_year": "new"})
    with pytest.raises(ValidationError) as excinfo:
        term.full_clean()
    assert excinfo.value.message_dict["attributes"] == ["model_year must be a number."]


@pytest.mark.parametrize("schema", [[{"kind": "text"}], ["model_year"], {"code": "x"}])
def test_a_term_of_a_taxonomy_with_an_invalid_schema_fails_validation(schema: object) -> None:
    # The schema is stored without validation (an import, a raw update): no KeyError.
    term = TermFactory.create(taxonomy=TaxonomyFactory.create(term_attributes=schema))
    with pytest.raises(ValidationError) as excinfo:
        term.full_clean()
    assert excinfo.value.message_dict["attributes"] == [
        "The taxonomy declares its term attributes wrongly: correct the taxonomy first."
    ]


def test_moving_a_term_under_a_term_of_a_flat_taxonomy_is_refused() -> None:
    taxonomy = TaxonomyFactory.create(hierarchical=False)
    first = TermFactory.create(taxonomy=taxonomy)
    second = TermFactory.create(taxonomy=taxonomy)
    for position in ("first-child", "last-child", "sorted-child"):
        with pytest.raises(ValidationError, match="This taxonomy is flat"):
            second.move(first, position)
    second.refresh_from_db()
    assert second.is_root()


def test_moving_a_term_under_a_term_of_another_taxonomy_is_refused() -> None:
    other = TermFactory.create(taxonomy=TaxonomyFactory.create(hierarchical=True))
    term = TermFactory.create(taxonomy=TaxonomyFactory.create(hierarchical=True))
    with pytest.raises(ValidationError, match="same taxonomy"):
        term.move(other, "last-child")
    with pytest.raises(ValidationError, match="same taxonomy"):
        term.move(TermFactory.create(parent=other), "left")  # a sibling of other's child
    term.refresh_from_db()
    assert term.is_root()


def test_moving_terms_within_their_taxonomy_is_allowed() -> None:
    taxonomy = TaxonomyFactory.create(hierarchical=True)
    maker = TermFactory.create(taxonomy=taxonomy)
    model = TermFactory.create(taxonomy=taxonomy)
    model.move(maker, "first-child")
    model.refresh_from_db()
    assert model.get_parent() == maker
    model.move(maker, "right")  # back to the top level, next to its old parent
    model.refresh_from_db()
    assert model.is_root()
    # Roots of every taxonomy are siblings: a flat taxonomy's term can go next to another root.
    flat = TermFactory.create(taxonomy=TaxonomyFactory.create(hierarchical=False))
    flat.move(maker, "left")
    flat.refresh_from_db()
    assert flat.is_root()


def test_sibling_terms_cannot_share_a_slug_in_one_language() -> None:
    taxonomy = TaxonomyFactory.create()
    TermTranslationFactory.create(term=TermFactory.create(taxonomy=taxonomy), slug="summer")
    duplicate = TermTranslationFactory.build(
        term=TermFactory.create(taxonomy=taxonomy), slug="summer"
    )
    with pytest.raises(ValidationError) as excinfo:
        duplicate.full_clean()
    assert excinfo.value.message_dict["slug"] == [
        "Another term at this level of the taxonomy already uses this slug in this language."
    ]


def test_terms_of_different_taxonomies_may_share_a_slug() -> None:
    TermTranslationFactory.create(term=TermFactory.create(), slug="summer")
    TermTranslationFactory.build(term=TermFactory.create(), slug="summer").full_clean()


def test_term_texts_carry_the_seo_texts() -> None:
    term = TermFactory.create()
    TermTranslationFactory.create(
        term=term,
        language="en",
        name="Summer",
        slug="summer",
        description="<p>Light products for hot days.</p>",
        seo_title="Summer products",
        seo_description="Light products for hot days.",
        status="published",
    )
    resolved = term.translation("de")
    assert resolved.is_fallback is True
    assert resolved.translation is not None
    assert resolved.translation.seo_title == "Summer products"
    assert str(term) == f"{term.taxonomy.code}: Summer"


# --- Brands


def test_brand_names_are_unique_ignoring_case() -> None:
    BrandFactory.create(name="Maker A")
    with pytest.raises(IntegrityError), transaction.atomic():
        BrandFactory.create(name="maker a")


def test_brand_slugs_are_unique_per_language() -> None:
    BrandTranslationFactory.create(language="en", slug="maker-a")
    BrandTranslationFactory.create(language="it", slug="maker-a")
    with pytest.raises(IntegrityError), transaction.atomic():
        BrandTranslationFactory.create(language="en", slug="maker-a")


def test_brand_texts_resolve_with_the_explicit_english_fallback() -> None:
    brand = BrandFactory.create(name="Maker A")
    BrandTranslationFactory.create(
        brand=brand, language="en", slug="maker-a", seo_title="Maker A", status="published"
    )
    assert brand.translation("fr").is_fallback is True
    assert str(brand) == "Maker A"


# --- Products


def test_a_product_links_to_its_categories_brand_and_terms() -> None:
    main = CategoryFactory.create()
    secondary = CategoryFactory.create()
    brand = BrandFactory.create()
    term = TermFactory.create()
    product = ProductFactory.create(category=main, brand=brand)
    product.secondary_categories.add(secondary)
    product.terms.add(term)
    product.refresh_from_db()
    assert product.category == main
    assert list(product.secondary_categories.all()) == [secondary]
    assert product.brand == brand
    assert list(product.terms.all()) == [term]
    assert list(main.products.all()) == [product]
    assert list(brand.products.all()) == [product]
    assert list(term.products.all()) == [product]


def test_a_category_or_brand_in_use_cannot_be_deleted() -> None:
    product = ProductFactory.create(category=CategoryFactory.create(), brand=BrandFactory.create())
    assert product.category is not None
    assert product.brand is not None
    with pytest.raises(ProtectedError), transaction.atomic():
        product.category.delete()
    with pytest.raises(ProtectedError), transaction.atomic():
        product.brand.delete()
    assert Category.objects.count() == 1
    assert Brand.objects.count() == 1


def test_upper_term_levels_are_derived_from_the_product_terms() -> None:
    maker = TermFactory.create()
    model = TermFactory.create(parent=maker)
    version = TermFactory.create(parent=model)
    unrelated = TermFactory.create(taxonomy=maker.taxonomy)
    product = ProductFactory.create()
    product.terms.add(version)
    assert set(services.effective_terms(product)) == {maker, model, version}
    assert unrelated not in services.effective_terms(product)


def test_a_term_lists_the_products_of_its_lower_levels() -> None:
    maker = TermFactory.create()
    model = TermFactory.create(parent=maker)
    tagged_with_model = ProductFactory.create()
    tagged_with_model.terms.add(model)
    tagged_with_both = ProductFactory.create()
    tagged_with_both.terms.add(maker, model)
    ProductFactory.create().terms.add(TermFactory.create())
    assert list(services.products_with_term(maker).order_by("sku")) == [
        tagged_with_model,
        tagged_with_both,
    ]
    assert list(services.products_with_term(model).order_by("sku")) == [
        tagged_with_model,
        tagged_with_both,
    ]


def test_a_category_lists_the_products_of_its_subtree_main_or_secondary() -> None:
    bags = CategoryFactory.create()
    backpacks = CategoryFactory.create(parent=bags)
    in_child = ProductFactory.create(category=backpacks)
    secondary = ProductFactory.create(category=CategoryFactory.create())
    secondary.secondary_categories.add(bags)
    ProductFactory.create(category=CategoryFactory.create())
    assert list(services.products_in_category(bags).order_by("sku")) == [in_child, secondary]
    assert list(services.products_in_category(bags, include_secondary=False)) == [in_child]
