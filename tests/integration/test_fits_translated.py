"""I18N.20: the Fits tab of the product page and the Fits pages of the shop space show no English
UI text under it, de, fr and es. Data names (makes, models, notes) are data and stay as entered."""

import re
from pathlib import Path
from typing import Any

import polib
import pytest
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from django.apps import apps
from django.test import Client
from django.urls import reverse
from django.utils import translation
from django.utils.translation import ngettext
from psycopg.types.range import Range
from wagtail.models import Site

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.fitment.models import ProductFitmentState
from apps.fitment.tests.factories import (
    FitmentNodeFactory,
    ProductFitmentFactory,
    ProductFitmentStateFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.integration.test_product_specs import make_product
from tests.unit.test_language_catalogs import FORMAL

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
LANGUAGES = ["it", "de", "fr", "es"]
REGISTER_LANGUAGES = ["de", "fr", "es"]  # Italian has no formal/informal pronoun check here
DEFAULT = "en"

STOREFRONT_SOURCES = [
    "apps/catalog/templates/catalog/includes/product_fitment.html",
    "apps/catalog/templates/catalog/includes/fitment_group.html",
    "apps/fitment/product_summary.py",
]
MANAGER_SOURCES = [
    "apps/manager/templates/manager/shop/fitment_list.html",
    "apps/manager/templates/manager/shop/_fitment_rows.html",
    "apps/manager/templates/manager/shop/fitment_product.html",
    "apps/manager/fitment_shop_views.py",
    "apps/manager/fitment_shop_forms.py",
]
# `{% translate "x" %}`, `_("x")`, `gettext_lazy("x")`, `{% translate 'x' %}` (no msgctxt).
CALL = re.compile(
    r"""(?:translate\s+|\b_\(\s*|gettext(?:_lazy)?\(\s*)(?P<q>["'])(?P<text>.+?)(?P=q)"""
)
BLOCK = re.compile(r"\{% blocktranslate[^%]*%\}(?P<text>.+?)\{% plural %\}", re.S)
# Strings that are the same word in a language (an abbreviation, a loanword).
SAME_BY_DESIGN: dict[str, set[str]] = {
    "it": {"SKU"},
    "de": {"SKU"},
    "fr": {"SKU", "Note"},
    "es": {"SKU"},
}


def _msgids(paths: list[str]) -> set[str]:
    """Every msgid the listed templates and modules ask for, found by scanning their source."""
    found: set[str] = set()
    for path in paths:
        source = (ROOT / path).read_text()
        found |= {m.group("text") for m in CALL.finditer(source)}
        for block in BLOCK.finditer(source):
            text = re.sub(r"\{\{\s*(\w+)\s*\}\}", r"%(\1)s", block.group("text"))
            found.add(text.strip())
    return {m for m in found if re.search(r"[A-Za-z]{2}", m)}


STOREFRONT_MSGIDS = _msgids(STOREFRONT_SOURCES) | {"Fits"}  # the heading is in product.html
MANAGER_MSGIDS = _msgids(MANAGER_SOURCES)


def _catalog(language: str) -> dict[str, str]:
    po = polib.pofile(str(ROOT / "locale" / language / "LC_MESSAGES" / "django.po"))
    return {e.msgid: e.msgstr or e.msgstr_plural[0] for e in po if not e.msgctxt and not e.obsolete}


def _expected(language: str, msgids: set[str]) -> dict[str, str]:
    """The catalog's msgstr per msgid; each must be a real translation, not the English text."""
    catalog = _catalog(language)
    assert not (msgids - set(catalog)), f"msgids missing from the {language} catalog"
    out = {m: catalog[m] for m in msgids}
    same = {m for m, t in out.items() if t == m and m not in SAME_BY_DESIGN.get(language, set())}
    assert not same, f"{language}: msgstr equals msgid for {sorted(same)}"
    return out


def _surfaces(html: bytes) -> set[str]:
    """Whole text nodes and whole attribute values of a page, whitespace collapsed."""
    soup = BeautifulSoup(html, "html.parser")
    found = {
        " ".join(node.split())
        for node in soup.find_all(string=True)
        if isinstance(node, NavigableString)
        and node.parent
        and node.parent.name not in ("script", "style")
    }
    for tag in soup.find_all(True):
        assert isinstance(tag, Tag)
        for value in tag.attrs.values():
            found |= {" ".join(v.split()) for v in ([value] if isinstance(value, str) else value)}
    found.discard("")
    return found


def _shows(nodes: set[str], text: str) -> bool:
    """`text` is a whole text node, or a whole-word start or end of one ("Not for" + a name)."""
    return any(n == text or n.startswith(text + " ") or n.endswith(" " + text) for n in nodes)


def _check(language: str, msgids: set[str], english: set[str], translated: set[str]) -> int:
    """Each msgid shown in the English page: its msgstr is a whole node (or the start of one) in
    `language`, and the English text is not a whole node (or the start of one) there."""
    expected = _expected(language, msgids)
    checked = 0
    for msgid, msgstr in expected.items():
        if "%(" in msgid or not _shows(english, msgid):
            continue
        checked += 1
        assert _shows(translated, msgstr), f"{language}: '{msgstr}' ({msgid!r}) is not rendered"
        assert msgstr == msgid or not _shows(translated, msgid), (
            f"{language}: English '{msgid}' is still rendered"
        )
    return checked


def _no_formal_address(language: str, texts: list[str]) -> None:
    for text in texts:
        assert not FORMAL[language].search(text), f"{language}: formal address in {text!r}"


@pytest.fixture(autouse=True)
def _indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture(params=["base", "ride"])
def theme(request: Any, settings: Any) -> str:
    settings.MPMV_THEME_DEFAULT = request.param
    return str(request.param)


def _page(client: Client, product: Any, language: str) -> bytes:
    """The product page in `language`: a translation per language, then the localized URL."""
    from django.conf import settings

    from apps.catalog.slugs import product_path
    from apps.catalog.tests.factories import ProductTranslationFactory

    for code in {*LANGUAGES, DEFAULT}:
        if not product.translations.filter(language=code).exists():
            ProductTranslationFactory.create(
                product=product,
                language=code,
                title=f"Mug {code}",
                slug=f"mug-{code}",
                status="published",
            )
    slug = product.translations.get(language=language).slug
    url = product_path(language, slug, default_language=settings.LANGUAGE_CODE)
    response = client.get(url)
    assert response.status_code == 200
    return bytes(response.content)


def _storefront_product(universal: bool) -> Any:
    product = make_product()
    if universal:
        ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.UNIVERSAL)
        return product
    ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.SPECIFIC)
    make = FitmentNodeFactory.create(name="Brand")
    for n in range(30):
        node = FitmentNodeFactory.create(parent=make, name=f"Roadster {n}")
        ProductFitmentFactory.create(product=product, node=node, years=Range(2015, 2021))
    ProductFitmentFactory.create(product=product, node=make, is_exclusion=True)
    open_ended = FitmentNodeFactory.create(parent=make, name="Open ended")
    ProductFitmentFactory.create(product=product, node=open_ended, years=Range(2015, None))
    return product


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_storefront_fits_panel_is_translated(client: Client, theme: str, language: str) -> None:
    product = _storefront_product(universal=False)
    english = _surfaces(_page(client, product, DEFAULT))
    translated_html = _page(client, product, language)
    translated = _surfaces(translated_html)
    # The panel really shows the strings under test (a guard against a vacuous pass).
    assert "Fits" in english
    assert "Not for All models" in english
    checked = _check(language, STOREFRONT_MSGIDS, english, translated)
    assert checked >= 2  # "Fits" and "Not for"
    # The plural summary and the exclusion line: exact nodes, built from the catalog.
    count = next(int(m.group(1)) for s in english if (m := re.fullmatch(r"Show (\d+) more", s)))
    assert count > 0
    catalog = _catalog(language)
    with translation.override(language):
        summary = ngettext("Show %(count)s more", "Show %(count)s more", count) % {"count": count}
    assert summary != f"Show {count} more"
    assert summary in translated
    assert f"Show {count} more" not in translated
    assert f"{catalog['Not for']} {catalog['All models']}" in translated
    assert "Not for All models" not in translated
    assert "(from 2015)" in english
    assert f"({catalog['from %(year)s'] % {'year': 2015}})" in translated
    assert "(from 2015)" not in translated


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_universal_text_is_translated(client: Client, theme: str, language: str) -> None:
    product = _storefront_product(universal=True)
    english = _surfaces(_page(client, product, DEFAULT))
    translated = _surfaces(_page(client, product, language))
    assert "Fits everything." in english
    assert (
        _check(language, STOREFRONT_MSGIDS, english, translated) >= 2
    )  # "Fits", "Fits everything."
    assert "Fits everything." not in translated


@pytest.mark.parametrize("language", REGISTER_LANGUAGES)
def test_the_storefront_fits_strings_use_the_informal_register(language: str) -> None:
    catalog = _catalog(language)
    _no_formal_address(language, [catalog[m] for m in STOREFRONT_MSGIDS])


@pytest.mark.parametrize("language", REGISTER_LANGUAGES)
def test_the_manager_fits_strings_use_the_formal_register(language: str) -> None:
    informal = {
        "de": re.compile(r"\b(du|dein\w*|dir|dich)\b", re.IGNORECASE),
        "fr": re.compile(r"\b(tu|ton|ta|tes|toi)\b", re.IGNORECASE),
        "es": re.compile(r"\b(tú|tu|tus|ti|contigo)\b", re.IGNORECASE),
    }[language]
    catalog = _catalog(language)
    for msgid in MANAGER_MSGIDS:
        assert not informal.search(catalog[msgid]), f"{language}: informal address in {msgid!r}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_shop_fits_pages_are_translated(client: Client, language: str) -> None:
    from apps.catalog.tests.factories import ProductFactory

    vendor = VendorFactory.create(display_name="Shop One", status=Vendor.Status.ACTIVE)
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    product = ProductFactory.create(vendor=vendor)
    urls = [
        reverse("manager:shop:fitment"),
        reverse("manager:shop:fitment_product", args=[product.public_id]),
    ]

    def surfaces(code: str) -> set[str]:
        User.objects.filter(pk=owner.pk).update(preferred_language=code)
        found: set[str] = set()
        for url in urls:
            response = client.get(url)
            assert response.status_code == 200
            found |= _surfaces(response.content)
        return found

    english = surfaces(DEFAULT)
    translated = surfaces(language)
    checked = _check(language, MANAGER_MSGIDS, english, translated)
    # Table headings, mode labels, picker data attributes: well beyond the three labels of before.
    assert checked >= 15
    # Every msgid has a real translation, also those only a later step of the page shows.
    _expected(language, MANAGER_MSGIDS)
