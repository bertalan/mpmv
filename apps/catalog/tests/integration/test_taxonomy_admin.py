"""CAT.2 (done when: admin CRUD with translations): staff manage the category tree, the
configurable taxonomies and their terms, and brands, with their texts in every language, and
link products to them."""

from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

import pytest
from django.contrib.admin.sites import site
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import (
    Brand,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Product,
    Taxonomy,
    TaxonomyTranslation,
    Term,
    TermTranslation,
)
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

MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def management_form(prefix: str, total: int = 0, initial: int = 0) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def translation_rows(rows: list[dict[str, str]], *, initial: int = 0) -> dict[str, str]:
    """Inline `translations` formset data, one dict of fields per row."""
    data = management_form("translations", total=len(rows), initial=initial)
    for i, row in enumerate(rows):
        data.update({f"translations-{i}-{name}": value for name, value in row.items()})
    return data


def texts(language: str, name: str, slug: str, **extra: str) -> dict[str, str]:
    row = {
        "language": language,
        "status": "published",
        "name": name,
        "slug": slug,
        "description": "",
        "seo_title": "",
        "seo_description": "",
    }
    row.update(extra)
    return row


def placement(ref: Category | Term | None = None, position: str = "first-child") -> dict[str, str]:
    return {
        "treebeard_position": position,
        "treebeard_ref_node": "" if ref is None else str(ref.pk),
    }


def errors_of(response: Any) -> str:
    return str(response.context["errors"]) if response.context else ""


# --- Categories


def test_add_a_root_category_with_translations_and_seo_texts(staff_client: Client) -> None:
    data = placement()
    data.update(
        translation_rows(
            [
                texts(
                    "en",
                    "Bags",
                    "bags",
                    description="<p>Hand-made bags.</p>",
                    seo_title="Hand-made bags",
                    seo_description="Bags made by hand.",
                ),
                texts("it", "Borse", "borse", status="machine"),
            ]
        )
    )
    response = staff_client.post(reverse("admin:catalog_category_add"), data)
    assert response.status_code == 302, errors_of(response)
    category = Category.objects.get()
    assert category.is_root()
    english = category.translations.get(language="en")
    assert (english.name, english.slug, english.seo_title) == ("Bags", "bags", "Hand-made bags")
    assert category.translations.get(language="it").status == "machine"


def test_add_a_subcategory_first_under_its_parent(staff_client: Client) -> None:
    bags = CategoryFactory.create()
    later = CategoryFactory.create(parent=bags)
    data = placement(bags, "first-child")
    data.update(translation_rows([texts("en", "Backpacks", "backpacks")]))
    response = staff_client.post(reverse("admin:catalog_category_add"), data)
    assert response.status_code == 302, errors_of(response)
    backpacks = CategoryTranslation.objects.get(slug="backpacks").category
    assert backpacks.get_parent() == bags
    assert list(Category.objects.get(pk=bags.pk).get_children()) == [backpacks, later]


def test_add_a_category_after_a_sibling(staff_client: Client) -> None:
    first = CategoryFactory.create()
    last = CategoryFactory.create()
    data = placement(first, "right")
    data.update(translation_rows([texts("en", "Hats", "hats")]))
    response = staff_client.post(reverse("admin:catalog_category_add"), data)
    assert response.status_code == 302, errors_of(response)
    hats = CategoryTranslation.objects.get(slug="hats").category
    assert list(Category.get_root_nodes()) == [first, hats, last]


def test_a_sibling_slug_conflict_is_refused_and_nothing_is_saved(staff_client: Client) -> None:
    CategoryTranslationFactory.create(category=CategoryFactory.create(), slug="bags")
    data = placement()
    data.update(translation_rows([texts("en", "Bags", "bags")]))
    response = staff_client.post(reverse("admin:catalog_category_add"), data)
    assert response.status_code == 200
    assert "already uses this slug" in response.content.decode()
    assert Category.objects.count() == 1  # the new node was rolled back


def test_changelist_shows_the_tree_with_indented_names(staff_client: Client) -> None:
    bags = CategoryFactory.create()
    CategoryTranslationFactory.create(category=bags, name="Bags")
    backpacks = CategoryFactory.create(parent=bags)
    CategoryTranslationFactory.create(category=backpacks, name="Backpacks")
    response = staff_client.get(reverse("admin:catalog_category_changelist"))
    assert response.status_code == 200
    assert list(response.context["cl"].result_list) == [Category.objects.get(pk=bags.pk), backpacks]
    content = response.content.decode()
    assert "Bags" in content
    assert "— Backpacks" in content


def test_change_texts_and_move_a_category_under_another(staff_client: Client) -> None:
    bags = CategoryFactory.create()
    hats = CategoryFactory.create()
    english = CategoryTranslationFactory.create(category=hats, name="Hats", slug="hats")
    data = placement(bags, "first-child")
    data.update(
        translation_rows(
            [{**texts("en", "Caps and hats", "hats"), "id": str(english.pk)}], initial=1
        )
    )
    response = staff_client.post(reverse("admin:catalog_category_change", args=[hats.pk]), data)
    assert response.status_code == 302, errors_of(response)
    hats.refresh_from_db()
    assert hats.get_parent() == bags
    assert hats.translations.get().name == "Caps and hats"


def test_moving_a_category_next_to_a_same_slug_sibling_is_refused(staff_client: Client) -> None:
    bags = CategoryFactory.create()
    CategoryTranslationFactory.create(category=CategoryFactory.create(parent=bags), slug="hats")
    hats = CategoryFactory.create()
    english = CategoryTranslationFactory.create(category=hats, name="Hats", slug="hats")
    data = placement(bags, "first-child")
    data.update(
        translation_rows([{**texts("en", "Hats", "hats"), "id": str(english.pk)}], initial=1)
    )
    response = staff_client.post(reverse("admin:catalog_category_change", args=[hats.pk]), data)
    assert response.status_code == 200
    assert "already uses this slug" in response.content.decode()
    hats.refresh_from_db()
    assert hats.is_root()  # the move was rolled back


def test_delete_a_leaf_category_with_its_translations(staff_client: Client) -> None:
    category = CategoryFactory.create(languages=["en", "it"])
    response = staff_client.post(
        reverse("admin:catalog_category_delete", args=[category.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert Category.objects.count() == 0
    assert CategoryTranslation.objects.count() == 0


def test_a_category_with_subcategories_cannot_be_deleted(staff_client: Client) -> None:
    parent = CategoryFactory.create()
    CategoryFactory.create(parent=parent)
    response = staff_client.post(
        reverse("admin:catalog_category_delete", args=[parent.pk]), {"post": "yes"}
    )
    assert response.status_code == 403
    assert Category.objects.count() == 2


def test_a_category_used_by_a_product_cannot_be_deleted(staff_client: Client) -> None:
    category = CategoryFactory.create()
    ProductFactory.create(category=category)
    response = staff_client.post(
        reverse("admin:catalog_category_delete", args=[category.pk]), {"post": "yes"}
    )
    assert response.status_code == 200  # the "protected objects" page
    assert Category.objects.filter(pk=category.pk).exists()


def test_tree_admins_offer_no_bulk_delete(superuser: User) -> None:
    request = RequestFactory().get("/")
    request.user = superuser
    for model in (Category, Term):
        assert "delete_selected" not in site._registry[model].get_actions(request)


# --- Taxonomies


def test_add_a_taxonomy_with_translated_names_and_slugs(staff_client: Client) -> None:
    data = {
        "code": "occasion",
        "hierarchical": "",
        "filterable": "on",
        "has_landing": "on",
        "term_attributes": "[]",
    }
    data.update(
        translation_rows(
            [
                {"language": "en", "status": "published", "name": "Occasion", "slug": "occasion"},
                {"language": "it", "status": "machine", "name": "Occasione", "slug": "occasione"},
            ]
        )
    )
    response = staff_client.post(reverse("admin:catalog_taxonomy_add"), data)
    assert response.status_code == 302, errors_of(response)
    taxonomy = Taxonomy.objects.get()
    assert (taxonomy.hierarchical, taxonomy.filterable, taxonomy.has_landing) == (
        False,
        True,
        True,
    )
    assert taxonomy.translations.get(language="it").slug == "occasione"


def test_a_taxonomy_with_a_malformed_term_schema_is_refused(staff_client: Client) -> None:
    data = {
        "code": "occasion",
        "term_attributes": '[{"code": "x", "kind": "date"}]',
    }
    data.update(translation_rows([]))
    response = staff_client.post(reverse("admin:catalog_taxonomy_add"), data)
    assert response.status_code == 200
    assert "the kind must be one of" in response.content.decode()
    assert Taxonomy.objects.count() == 0


def test_change_and_delete_a_taxonomy(staff_client: Client) -> None:
    taxonomy = TaxonomyFactory.create(code="season")
    english = TaxonomyTranslationFactory.create(taxonomy=taxonomy, name="Season", slug="season")
    data = {
        "code": "season",
        "hierarchical": "on",
        "filterable": "on",
        "has_landing": "",
        "term_attributes": "[]",
    }
    data.update(
        translation_rows(
            [
                {
                    "id": str(english.pk),
                    "language": "en",
                    "status": "published",
                    "name": "Seasons",
                    "slug": "seasons",
                }
            ],
            initial=1,
        )
    )
    url = reverse("admin:catalog_taxonomy_change", args=[taxonomy.pk])
    response = staff_client.post(url, data)
    assert response.status_code == 302, errors_of(response)
    assert taxonomy.translations.get().slug == "seasons"

    response = staff_client.post(
        reverse("admin:catalog_taxonomy_delete", args=[taxonomy.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert Taxonomy.objects.count() == 0
    assert TaxonomyTranslation.objects.count() == 0


# --- Terms


def term_form(taxonomy: Taxonomy | None, **extra: str) -> dict[str, str]:
    data = {"attributes": "{}"}
    if taxonomy is not None:
        data["taxonomy"] = str(taxonomy.pk)
    data.update(extra)
    return data


def test_add_terms_on_two_levels_with_translations(staff_client: Client) -> None:
    taxonomy = TaxonomyFactory.create(
        hierarchical=True, term_attributes=[{"code": "model_year", "kind": "number"}]
    )
    data = term_form(taxonomy, attributes='{"model_year": 2020}', **placement())
    data.update(translation_rows([texts("en", "Maker A", "maker-a", seo_title="Maker A parts")]))
    response = staff_client.post(reverse("admin:catalog_term_add"), data)
    assert response.status_code == 302, errors_of(response)
    maker = Term.objects.get()
    assert maker.attributes == {"model_year": 2020}
    assert maker.translations.get().seo_title == "Maker A parts"

    data = term_form(taxonomy, **placement(maker, "first-child"))
    data.update(translation_rows([texts("en", "Model 1", "model-1")]))
    response = staff_client.post(reverse("admin:catalog_term_add"), data)
    assert response.status_code == 302, errors_of(response)
    child = TermTranslation.objects.get(slug="model-1").term
    assert child.get_parent() == maker


def test_a_flat_taxonomy_refuses_a_child_term_in_the_admin(staff_client: Client) -> None:
    root = TermFactory.create(taxonomy=TaxonomyFactory.create(hierarchical=False))
    data = term_form(root.taxonomy, **placement(root, "first-child"))
    data.update(translation_rows([texts("en", "Beach", "beach")]))
    response = staff_client.post(reverse("admin:catalog_term_add"), data)
    assert response.status_code == 200
    assert "This taxonomy is flat" in response.content.decode()
    assert Term.objects.count() == 1


def test_a_flat_taxonomy_accepts_a_term_placed_after_another(staff_client: Client) -> None:
    root = TermFactory.create(taxonomy=TaxonomyFactory.create(hierarchical=False))
    data = term_form(root.taxonomy, **placement(root, "right"))
    data.update(translation_rows([texts("en", "Beach", "beach")]))
    response = staff_client.post(reverse("admin:catalog_term_add"), data)
    assert response.status_code == 302, errors_of(response)
    assert TermTranslation.objects.get(slug="beach").term.is_root()


def test_a_term_cannot_be_placed_under_another_taxonomy(staff_client: Client) -> None:
    other = TermFactory.create()
    data = term_form(TaxonomyFactory.create(), **placement(other, "first-child"))
    data.update(translation_rows([texts("en", "Beach", "beach")]))
    response = staff_client.post(reverse("admin:catalog_term_add"), data)
    assert response.status_code == 200
    assert "same taxonomy" in response.content.decode()
    assert Term.objects.count() == 1


def test_moving_a_term_under_another_in_a_flat_taxonomy_is_refused(staff_client: Client) -> None:
    taxonomy = TaxonomyFactory.create(hierarchical=False)
    first, _ = term_of(taxonomy, "Beach")
    second, english = term_of(taxonomy, "Snow")
    data = term_form(None, **placement(first, "first-child"))
    data.update(
        translation_rows([{**texts("en", "Snow", "snow"), "id": str(english.pk)}], initial=1)
    )
    response = staff_client.post(reverse("admin:catalog_term_change", args=[second.pk]), data)
    assert response.status_code == 200
    assert "This taxonomy is flat" in response.content.decode()
    assert Term.objects.get(pk=second.pk).is_root()


def test_moving_a_term_next_to_a_same_slug_sibling_is_refused(staff_client: Client) -> None:
    taxonomy = TaxonomyFactory.create(hierarchical=True)
    maker, _ = term_of(taxonomy, "Maker")
    TermTranslationFactory.create(term=TermFactory.create(parent=maker), slug="model")
    model, english = term_of(taxonomy, "Model")
    data = term_form(None, **placement(maker, "first-child"))
    data.update(
        translation_rows([{**texts("en", "Model", "model"), "id": str(english.pk)}], initial=1)
    )
    response = staff_client.post(reverse("admin:catalog_term_change", args=[model.pk]), data)
    assert response.status_code == 200
    assert "already uses this slug" in response.content.decode()
    assert Term.objects.get(pk=model.pk).is_root()  # the move was rolled back


def test_term_attributes_outside_the_schema_are_refused(staff_client: Client) -> None:
    taxonomy = TaxonomyFactory.create(term_attributes=[])
    data = term_form(taxonomy, attributes='{"colour": "red"}', **placement())
    data.update(translation_rows([texts("en", "Red", "red")]))
    response = staff_client.post(reverse("admin:catalog_term_add"), data)
    assert response.status_code == 200
    assert "colour is not an attribute of this taxonomy." in response.content.decode()
    assert Term.objects.count() == 0


def test_change_a_term_keeps_its_taxonomy(staff_client: Client) -> None:
    term = TermFactory.create()
    english = TermTranslationFactory.create(term=term, name="Summer", slug="summer")
    data = term_form(TaxonomyFactory.create(), **placement())  # the taxonomy is read-only
    data.update(
        translation_rows(
            [{**texts("en", "Summer time", "summer"), "id": str(english.pk)}], initial=1
        )
    )
    response = staff_client.post(reverse("admin:catalog_term_change", args=[term.pk]), data)
    assert response.status_code == 302, errors_of(response)
    changed = Term.objects.get(pk=term.pk)
    assert changed.taxonomy == term.taxonomy
    assert changed.translations.get().name == "Summer time"


class SelectedOptions(HTMLParser):
    """The value a browser submits for each `<select>`: its selected option, else its first."""

    def __init__(self) -> None:
        super().__init__()
        self.first: dict[str, str] = {}
        self.selected: dict[str, str] = {}
        self._select: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "select":
            self._select = attributes.get("name")
        elif tag == "option" and self._select is not None:
            value = attributes.get("value") or ""
            self.first.setdefault(self._select, value)
            if "selected" in attributes:
                self.selected[self._select] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select = None

    def submitted(self, name: str) -> str:
        return self.selected.get(name, self.first[name])


def rendered_placement(response: Any) -> dict[str, str]:
    """The placement a browser posts back from a change form left untouched."""
    parser = SelectedOptions()
    parser.feed(response.content.decode())
    return {name: parser.submitted(name) for name in ("treebeard_position", "treebeard_ref_node")}


def term_of(taxonomy: Taxonomy, name: str) -> tuple[Term, TermTranslation]:
    term = TermFactory.create(taxonomy=taxonomy)
    return term, TermTranslationFactory.create(term=term, name=name, slug=name.lower())


def test_saving_a_root_term_unchanged_keeps_its_place(staff_client: Client) -> None:
    occasion = TaxonomyFactory.create(code="occasion")
    season = TaxonomyFactory.create(code="season")
    o1, _ = term_of(occasion, "O1")
    s1, _ = term_of(season, "S1")
    o2, english = term_of(occasion, "O2")  # roots: O1, S1, O2
    url = reverse("admin:catalog_term_change", args=[o2.pk])
    form = staff_client.get(url)
    assert form.status_code == 200
    data = term_form(None, **rendered_placement(form))
    data.update(
        translation_rows([{**texts("en", "O2 renamed", "o2"), "id": str(english.pk)}], initial=1)
    )
    response = staff_client.post(url, data)
    assert response.status_code == 302, errors_of(response)
    assert list(Term.get_root_nodes()) == [o1, s1, o2]
    assert list(Term.get_root_nodes().filter(taxonomy=occasion)) == [o1, o2]
    assert Term.objects.get(pk=o2.pk).translations.get().name == "O2 renamed"


def test_the_root_option_moves_a_term_first_within_its_taxonomy(staff_client: Client) -> None:
    occasion = TaxonomyFactory.create(code="occasion")
    s1, _ = term_of(TaxonomyFactory.create(code="season"), "S1")
    o1, _ = term_of(occasion, "O1")
    o2, english = term_of(occasion, "O2")  # roots: S1, O1, O2
    data = term_form(None, **placement())
    data.update(translation_rows([{**texts("en", "O2", "o2"), "id": str(english.pk)}], initial=1))
    response = staff_client.post(reverse("admin:catalog_term_change", args=[o2.pk]), data)
    assert response.status_code == 302, errors_of(response)
    assert list(Term.get_root_nodes()) == [s1, o2, o1]  # the season terms are not passed over


def test_term_changelist_filters_by_taxonomy(staff_client: Client) -> None:
    mine = TermFactory.create()
    TermFactory.create()
    response = staff_client.get(
        reverse("admin:catalog_term_changelist"), {"taxonomy__id__exact": mine.taxonomy.pk}
    )
    assert response.status_code == 200
    assert list(response.context["cl"].result_list) == [mine]


def test_delete_a_term(staff_client: Client) -> None:
    term = TermFactory.create()
    TermTranslationFactory.create(term=term)
    product = ProductFactory.create()
    product.terms.add(term)
    response = staff_client.post(
        reverse("admin:catalog_term_delete", args=[term.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert Term.objects.count() == 0
    assert TermTranslation.objects.count() == 0
    assert product.terms.count() == 0


# --- Brands


def brand_texts(language: str, slug: str, **extra: str) -> dict[str, str]:
    row = {
        "language": language,
        "status": "published",
        "slug": slug,
        "description": "",
        "seo_title": "",
        "seo_description": "",
    }
    row.update(extra)
    return row


def test_add_change_and_delete_a_brand_with_translations(staff_client: Client) -> None:
    data = {"name": "Maker A"}
    data.update(
        translation_rows(
            [
                brand_texts("en", "maker-a", seo_title="Maker A products"),
                brand_texts("it", "maker-a", status="machine"),
            ]
        )
    )
    response = staff_client.post(reverse("admin:catalog_brand_add"), data)
    assert response.status_code == 302, errors_of(response)
    brand = Brand.objects.get()
    assert brand.translations.get(language="en").seo_title == "Maker A products"

    english = brand.translations.get(language="en")
    italian = brand.translations.get(language="it")
    data = {"name": "Maker A Works"}
    data.update(
        translation_rows(
            [
                {**brand_texts("en", "maker-a-works"), "id": str(english.pk)},
                {**brand_texts("it", "maker-a", status="machine"), "id": str(italian.pk)},
            ],
            initial=2,
        )
    )
    response = staff_client.post(reverse("admin:catalog_brand_change", args=[brand.pk]), data)
    assert response.status_code == 302, errors_of(response)
    brand.refresh_from_db()
    assert brand.name == "Maker A Works"
    assert brand.translations.get(language="en").slug == "maker-a-works"

    response = staff_client.post(
        reverse("admin:catalog_brand_delete", args=[brand.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert Brand.objects.count() == 0
    assert BrandTranslation.objects.count() == 0


def test_brand_changelist_searches_by_name(staff_client: Client) -> None:
    maker = BrandFactory.create(name="Maker A")
    BrandTranslationFactory.create(brand=maker)
    BrandFactory.create(name="Studio B")
    response = staff_client.get(reverse("admin:catalog_brand_changelist"), {"q": "maker"})
    assert list(response.context["cl"].result_list) == [maker]


# --- Products


def test_link_a_product_to_categories_brand_and_terms_in_the_admin(staff_client: Client) -> None:
    product = ProductFactory.create(sku="BAG-01")
    main = CategoryFactory.create()
    secondary = CategoryFactory.create()
    brand = BrandFactory.create()
    term = TermFactory.create()
    data: dict[str, Any] = {
        "vendor": str(product.vendor.pk),
        "sku": "BAG-01",
        "kind": "simple",
        "sales_mode": "buy",
        "fiscal_category": "standard",
        "price_net": str(product.price_net),
        "stock_policy": "track",
        "stock_qty": "0",
        "stock_status": "in_stock",
        "weight_kg": str(product.weight_kg),
        "category": str(main.pk),
        "secondary_categories": [str(secondary.pk)],
        "brand": str(brand.pk),
        "terms": [str(term.pk)],
    }
    data.update(management_form("translations"))
    data.update(management_form("variants"))
    response = staff_client.post(reverse("admin:catalog_product_change", args=[product.pk]), data)
    assert response.status_code == 302, errors_of(response)
    product = Product.objects.get(pk=product.pk)
    assert (product.category, product.brand) == (main, brand)
    assert list(product.secondary_categories.all()) == [secondary]
    assert list(product.terms.all()) == [term]


def test_the_product_form_shows_categories_as_an_indented_tree(staff_client: Client) -> None:
    bags = CategoryFactory.create()
    CategoryTranslationFactory.create(category=bags, name="Bags")
    backpacks = CategoryFactory.create(parent=bags)
    CategoryTranslationFactory.create(category=backpacks, name="Backpacks")
    product = ProductFactory.create()
    response = staff_client.get(reverse("admin:catalog_product_change", args=[product.pk]))
    assert response.status_code == 200
    assert "&nbsp;&nbsp;&nbsp;&nbsp;Backpacks" in response.content.decode()


# --- Widgets that scale: terms are looked up by autocomplete, not listed in full


class SelectOptions(HTMLParser):
    """The option values a page renders for the `<select>` named `name`, and its attributes."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.values: list[str] = []
        self.attributes: dict[str, str | None] = {}
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "select" and attributes.get("name") == self.name:
            self._inside = True
            self.attributes = attributes
        elif tag == "option" and self._inside:
            self.values.append(attributes.get("value") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._inside = False


def select_of(response: Any, name: str) -> SelectOptions:
    parser = SelectOptions(name)
    parser.feed(response.content.decode())
    return parser


def test_the_product_form_renders_only_the_chosen_terms(staff_client: Client) -> None:
    product = ProductFactory.create()
    chosen = TermFactory.create()
    TermFactory.create()  # not rendered: found by autocomplete
    product.terms.add(chosen)
    response = staff_client.get(reverse("admin:catalog_product_change", args=[product.pk]))
    assert response.status_code == 200
    select = select_of(response, "terms")
    assert select.values == [str(chosen.pk)]
    assert "admin-autocomplete" in (select.attributes.get("class") or "")


def test_the_term_reference_renders_only_the_current_one(staff_client: Client) -> None:
    taxonomy = TaxonomyFactory.create()
    first, _ = term_of(taxonomy, "First")
    second, _ = term_of(taxonomy, "Second")  # after First
    term_of(TaxonomyFactory.create(), "Other")
    response = staff_client.get(reverse("admin:catalog_term_change", args=[second.pk]))
    assert response.status_code == 200
    select = select_of(response, "treebeard_ref_node")
    assert select.values == ["", str(first.pk)]  # "" is the top level
    assert "admin-autocomplete" in (select.attributes.get("class") or "")
    assert f"taxonomy={taxonomy.pk}" in (select.attributes.get("data-ajax--url") or "")


def test_a_new_term_reference_follows_the_taxonomy_given_in_the_url(
    staff_client: Client,
) -> None:
    taxonomy = TaxonomyFactory.create()
    response = staff_client.get(reverse("admin:catalog_term_add"), {"taxonomy": taxonomy.pk})
    assert response.status_code == 200
    select = select_of(response, "treebeard_ref_node")
    assert "admin-autocomplete" in (select.attributes.get("class") or "")
    assert f"taxonomy={taxonomy.pk}" in (select.attributes.get("data-ajax--url") or "")


def autocomplete(client: Client, **params: Any) -> list[str]:
    query = {"app_label": "catalog", "model_name": "product", "field_name": "terms", **params}
    response = client.get(reverse("admin:autocomplete"), query)
    assert response.status_code == 200
    return [row["id"] for row in response.json()["results"]]


def test_term_autocomplete_searches_names_and_taxonomy_codes(staff_client: Client) -> None:
    season = TaxonomyFactory.create(code="season")
    summer, _ = term_of(season, "Summer")
    summit, _ = term_of(TaxonomyFactory.create(code="peak"), "Summit")
    assert set(autocomplete(staff_client, term="sum")) == {str(summer.pk), str(summit.pk)}
    assert autocomplete(staff_client, term="season") == [str(summer.pk)]


def test_term_autocomplete_is_narrowed_to_one_taxonomy(staff_client: Client) -> None:
    season = TaxonomyFactory.create()
    summer, _ = term_of(season, "Summer")
    term_of(TaxonomyFactory.create(), "Summit")
    assert autocomplete(staff_client, term="sum", taxonomy=season.pk) == [str(summer.pk)]
    assert autocomplete(staff_client, term="sum", taxonomy="not-a-number") == []


@pytest.mark.parametrize("taxonomy", ["\u00b2", "\u0661", "9" * 30, "1" * 5000])
def test_term_autocomplete_with_a_hostile_taxonomy_id_finds_nothing(
    staff_client: Client, taxonomy: str
) -> None:
    """Unicode digits pass `str.isdigit` but not `int()`; a huge id overflows the column."""
    term_of(TaxonomyFactory.create(), "Summer")
    assert autocomplete(staff_client, term="sum", taxonomy=taxonomy) == []
