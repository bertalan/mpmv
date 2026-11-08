"""CMS.2 (part 2/2): page, term and vendor cards on the home page, and the #109 review fixes.

Done when: "An editor builds the home page without code". A "featured links" block shows cards
for a page, a catalog term (its name and its spec 03 URL in the page's language, or the primary
language as fallback) and an active vendor's storefront. A card whose target is not public (a
draft page, a term with no published text, a vendor that is not active) is left out. The admin
choosers offer every term and only active vendors.

Review fixes of #109: several FAQ blocks give one FAQPage, and a link to a page that is not
live is dropped.
"""

import json
import re
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Term
from apps.catalog.tests.factories import (
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.cms.blocks import LinkBlock, VendorChooserBlock
from apps.cms.models import HomePage, StandardPage
from apps.cms.services import SeedReport
from apps.cms.tests.factories import StandardPageFactory
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """wagtail-localize publishes a translation on commit; a test transaction never commits."""
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _home(code: str = "en") -> HomePage:
    home: HomePage = HomePage.objects.get(locale__language_code=code)
    return home


def _about(code: str = "en") -> StandardPage:
    about: StandardPage = StandardPage.objects.get(
        locale__language_code=code, depth=_home(code).depth + 1
    )
    return about


def _publish(page: HomePage, body: list[tuple[str, Any]]) -> str:
    page.body = body
    page.save_revision().publish()
    return Client().get(page.url).content.decode()


def _featured(*cards: tuple[str, dict[str, Any]], heading: str = "Featured") -> tuple[str, Any]:
    return ("featured", {"heading": heading, "cards": list(cards)})


def _card(kind: str, target: Any, label: str = "", text: str = "") -> tuple[str, dict[str, Any]]:
    return (kind, {kind: target, "label": label, "text": text})


@pytest.fixture
def model_term() -> Term:
    """A child term with EN and IT texts: maker "Acme" → model "Roadster"."""
    taxonomy = TaxonomyFactory.create(code="compatibility")
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", name="Fits", slug="fits", status=PUBLISHED
    )
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="it", name="Compatibile", slug="compatibile", status=PUBLISHED
    )
    maker = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(
        term=maker, language="en", name="Acme", slug="acme", status=PUBLISHED
    )
    TermTranslationFactory.create(
        term=maker, language="it", name="Acme", slug="acme-it", status=PUBLISHED
    )
    model: Term = TermFactory.create(parent=maker)
    TermTranslationFactory.create(
        term=model, language="en", name="Roadster", slug="roadster", status=PUBLISHED
    )
    TermTranslationFactory.create(
        term=model, language="it", name="Spider", slug="spider", status=PUBLISHED
    )
    return model


@pytest.fixture
def english_only_term() -> Term:
    taxonomy = TaxonomyFactory.create(code="occasion", hierarchical=False)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", name="Occasion", slug="occasion", status=PUBLISHED
    )
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="it", name="Occasione", slug="occasione", status=PUBLISHED
    )
    term: Term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(
        term=term, language="en", name="Weddings", slug="weddings", status=PUBLISHED
    )
    return term


@pytest.fixture
def pottery() -> Vendor:
    vendor: Vendor = VendorFactory.create(
        display_name="Pottery Studio", slug="pottery-studio", status=Vendor.Status.ACTIVE
    )
    VendorTranslationFactory.create(
        vendor=vendor, language="en", tagline="Hand-thrown bowls", status=PUBLISHED
    )
    return vendor


@pytest.fixture
def editor() -> User:
    user: User = UserFactory.create(superuser=True)
    return user


# --- Term cards


def test_a_term_card_links_to_its_taxonomy_url_with_its_name(
    seeded: SeedReport, model_term: Term
) -> None:
    html = _publish(_home(), [_featured(_card("term", model_term))])
    assert '<a href="/fits/acme/roadster/">Roadster</a>' in html


def test_a_term_card_follows_the_page_language(seeded: SeedReport, model_term: Term) -> None:
    html = _publish(_home("it"), [_featured(_card("term", model_term))])
    assert '<a href="/it/compatibile/acme-it/spider/">Spider</a>' in html


def test_a_term_card_falls_back_to_the_primary_language(
    seeded: SeedReport, english_only_term: Term
) -> None:
    html = _publish(_home("it"), [_featured(_card("term", english_only_term))])
    assert '<a href="/occasion/weddings/">Weddings</a>' in html


def test_a_term_card_label_replaces_the_term_name(seeded: SeedReport, model_term: Term) -> None:
    html = _publish(
        _home(), [_featured(_card("term", model_term, label="Roadster parts", text="All of it"))]
    )
    assert '<a href="/fits/acme/roadster/">Roadster parts</a>' in html
    assert "All of it" in html


def test_a_term_without_published_texts_gets_no_card(seeded: SeedReport) -> None:
    term = TermFactory.create()
    TermTranslationFactory.create(term=term, language="en", name="Withheld", slug="withheld")
    html = _publish(_home(), [_featured(_card("term", term, label="Withheld"))])
    assert "Withheld" not in html


# --- Vendor cards


def test_a_vendor_card_links_to_the_storefront(seeded: SeedReport, pottery: Vendor) -> None:
    html = _publish(_home(), [_featured(_card("vendor", pottery))])
    assert '<a href="/shop/pottery-studio/">Pottery Studio</a>' in html
    assert "Hand-thrown bowls" in html


def test_a_vendor_card_follows_the_page_language(seeded: SeedReport, pottery: Vendor) -> None:
    html = _publish(_home("it"), [_featured(_card("vendor", pottery))])
    assert '<a href="/it/shop/pottery-studio/">Pottery Studio</a>' in html


def test_a_vendor_that_is_no_longer_active_gets_no_card(
    seeded: SeedReport, pottery: Vendor
) -> None:
    home = _home()
    home.body = [_featured(_card("vendor", pottery))]
    home.save_revision().publish()
    Vendor.objects.filter(pk=pottery.pk).update(status=Vendor.Status.SUSPENDED)
    html = Client().get(home.url).content.decode()
    assert "Pottery Studio" not in html
    assert "/shop/pottery-studio/" not in html


def test_only_an_active_vendor_can_be_chosen(seeded: SeedReport, pottery: Vendor) -> None:
    block = VendorChooserBlock()
    assert block.clean(pottery) == pottery
    applicant = VendorFactory.create(display_name="Applicant Shop")
    with pytest.raises(ValidationError):
        block.clean(applicant)


# --- Page cards and the featured block


def test_a_page_card_links_to_the_page_with_its_title(seeded: SeedReport) -> None:
    html = _publish(_home(), [_featured(_card("page", _about()))])
    assert f'<a href="{_about().url}">{_about().title}</a>' in html


def test_a_card_to_a_draft_page_is_left_out(seeded: SeedReport) -> None:
    _about().unpublish()
    html = _publish(_home(), [_featured(_card("page", _about(), label="Our story"))])
    assert "Our story" not in html


def test_a_featured_block_without_public_cards_is_left_out(
    seeded: SeedReport, pottery: Vendor
) -> None:
    Vendor.objects.filter(pk=pottery.pk).update(status=Vendor.Status.CLOSED)
    html = _publish(_home(), [_featured(_card("vendor", pottery), heading="Our makers")])
    assert "Our makers" not in html


def test_cards_keep_their_order(seeded: SeedReport, model_term: Term, pottery: Vendor) -> None:
    html = _publish(
        _home(),
        [_featured(_card("vendor", pottery), _card("term", model_term), _card("page", _about()))],
    )
    cards = html[html.index("<h2>Featured</h2>") :]  # the About page is in the menu too
    positions = [cards.index(text) for text in ("Pottery Studio", "Roadster", _about().title)]
    assert positions == sorted(positions)
    assert "style=" not in html


# --- Admin choosers


def test_the_editor_offers_the_featured_links_block(
    client: Client, seeded: SeedReport, editor: User
) -> None:
    verified_login(client, editor)
    response = client.get(reverse("wagtailadmin_pages:edit", args=(_home().pk,)))
    assert response.status_code == 200
    html = response.content.decode()
    for label in ("Featured links", "Term", "Vendor"):
        assert label in html, label


def _modal_html(client: Client, url_name: str) -> str:
    response = client.get(reverse(url_name))
    assert response.status_code == 200
    return str(json.loads(response.content)["html"])


def test_the_term_chooser_lists_terms(
    client: Client, seeded: SeedReport, editor: User, model_term: Term
) -> None:
    verified_login(client, editor)
    html = _modal_html(client, "cms_term_chooser:choose")
    assert "Roadster" in html


def test_the_vendor_chooser_lists_only_active_vendors(
    client: Client, seeded: SeedReport, editor: User, pottery: Vendor
) -> None:
    applicant = VendorFactory.create(display_name="Applicant Shop")
    verified_login(client, editor)
    html = _modal_html(client, "cms_vendor_chooser:choose")
    assert "Pottery Studio" in html
    assert "Applicant Shop" not in html
    chosen = client.get(reverse("cms_vendor_chooser:chosen", args=(applicant.pk,)))
    assert chosen.status_code == 404


def test_the_choosers_need_the_admin(client: Client, seeded: SeedReport) -> None:
    response = client.get(reverse("cms_vendor_chooser:choose"))
    assert response.status_code in (302, 403)


# --- Review fixes of #109


def _faq(question: str) -> tuple[str, dict[str, Any]]:
    return ("faq", {"heading": "", "items": [{"question": question, "answer": "<p>Yes.</p>"}]})


def test_several_faq_blocks_give_one_faq_page(seeded: SeedReport) -> None:
    html = _publish(_home(), [_faq("Do you ship abroad?"), _faq("Can I return an item?")])
    ld = [
        json.loads(data)
        for data in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    ]
    assert [data["@type"] for data in ld] == ["FAQPage"]
    assert [question["name"] for question in ld[0]["mainEntity"]] == [
        "Do you ship abroad?",
        "Can I return an item?",
    ]


def test_a_page_without_faq_has_no_faq_page(seeded: SeedReport) -> None:
    html = _publish(_home(), [("paragraph", "<p>Welcome.</p>")])
    assert "FAQPage" not in html


def test_a_link_to_a_page_that_is_not_live_has_no_href(seeded: SeedReport) -> None:
    _about().unpublish()
    block = LinkBlock()
    value = block.clean(block.to_python({"page": _about().pk, "url": "", "label": "About"}))
    assert value.href() == ""
    html = _publish(
        _home(),
        [
            (
                "cta",
                {
                    "heading": "Read more",
                    "text": "",
                    "link": {"page": _about(), "url": "", "label": "Our story"},
                },
            )
        ],
    )
    assert "Our story</a>" not in html


# --- No N+1 (#117 review): cards are resolved in bulk


def _two_level_term(n: int) -> Term:
    taxonomy = TaxonomyFactory.create(code=f"bulk-{n}")
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", name=f"Tax {n}", slug=f"tax-{n}", status=PUBLISHED
    )
    parent = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(
        term=parent, language="en", name=f"Maker {n}", slug=f"maker-{n}", status=PUBLISHED
    )
    child: Term = TermFactory.create(parent=parent)
    TermTranslationFactory.create(
        term=child, language="en", name=f"Model {n}", slug=f"model-{n}", status=PUBLISHED
    )
    return child


def _vendor(n: int) -> Vendor:
    vendor: Vendor = VendorFactory.create(
        display_name=f"Maker shop {n}", slug=f"maker-shop-{n}", status=Vendor.Status.ACTIVE
    )
    VendorTranslationFactory.create(
        vendor=vendor, language="en", tagline=f"Tagline {n}", status=PUBLISHED
    )
    return vendor


def _queries_to_render(numbers: range) -> tuple[int, str]:
    cards: list[tuple[str, dict[str, Any]]] = []
    for n in numbers:
        cards.append(_card("term", _two_level_term(n)))
        cards.append(_card("vendor", _vendor(n)))
        cards.append(_card("page", StandardPageFactory.create(title=f"Story {n}")))
    home = _home()
    home.body = [_featured(*cards)]
    home.save_revision().publish()
    client = Client()
    client.get(home.url)  # warm caches (site root paths, locales)
    with CaptureQueriesContext(connection) as queries:
        html = client.get(home.url).content.decode()
    return len(queries), html


def test_featured_cards_render_with_a_constant_number_of_queries(seeded: SeedReport) -> None:
    one, _ = _queries_to_render(range(100, 101))
    many, html = _queries_to_render(range(8))
    for n in range(8):
        assert f'<a href="/tax-{n}/maker-{n}/model-{n}/">Model {n}</a>' in html
        assert f'<a href="/shop/maker-shop-{n}/">Maker shop {n}</a>' in html
        assert f"Story {n}</a>" in html
    assert many <= one
