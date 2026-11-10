"""I18N.11 sweep: the language switcher does what it was built for on EVERY page.

For each route class (CMS pages, events, blog, listings, storefronts, landing pages, legal pages,
secret-bearing pages, the account, MFA and Manager areas, the admins) the test builds a real
object with factories, renders the page, and reads the switcher exactly as a browser would: the
form's action, its hidden fields, the CSRF token and the button. It then POSTs that form for each
configured language (the current one is disabled in the page, so it is skipped), follows the
redirect, and asserts:

- the answer is 200 and `<html lang>` is the target language;
- the page is the same object in that language (the independently computed translation URL), or
  the documented fallback: the target language's home with a one-time notice when the object has
  no translation, or the page's own path translated when the page has no object;
- a page whose URL carries a secret never echoes it into the HTML, before or after the switch.

Pages with no switcher on purpose (error pages, the Django admin, the editors' admin) are asserted
to have none. Unprefixed areas (the Manager, the account and MFA pages, sign-in) cannot switch
language for anonymous visitors: those cases are `xfail(strict=True)` until lane-a decides.

Adding a language to `settings.LANGUAGES` adds a column to the sweep; adding a page class means
adding one entry to `CASES` (the routes themselves are walked by
`apps/core/tests/integration/test_language_switch_secrets.py`).
"""

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext
from wagtail.models import Locale, Page

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
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
from apps.cms.seed import LEGAL_PLACEHOLDER_TITLES, SEED_TEXTS
from apps.cms.services import seed_site, translate_page
from apps.cms.tests.factories import (
    BlogIndexPageFactory,
    BlogPostPageFactory,
    LegalPageFactory,
    StandardPageFactory,
    VendorLandingPageFactory,
    VendorRecruitmentPageFactory,
)
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.events.models import EventIndexPage
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import (
    VendorFactory,
    VendorMemberFactory,
    VendorTranslationFactory,
)

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
LANGUAGES = configured_languages()
NOTICE_MSGID = "This content is not available in this language."
ANONYMOUS_UNPREFIXED = "anonymous language choice on unprefixed pages: decision pending (lane-a)"


def home_of(code: str) -> str:
    return "/" if code == settings.LANGUAGE_CODE else f"/{code}/"


@dataclass
class Case:
    """One page of a route class: where it is and where the switcher must lead."""

    url: str
    # {language: (path the switch lands on, a notice is shown)}. Empty: the page has no switcher.
    expect: dict[str, tuple[str, bool]] = field(default_factory=dict)
    user: User | None = None
    mfa: bool = False  # sign the user in with a verified second factor (Manager, staff areas)
    secret: str = ""  # must never appear in the page or in the page after the switch
    no_switcher: bool = False
    landing_status: int = 200  # what the page after the switch answers
    status: int = 200  # what the page itself answers (an error page, an invalid secret key)

    @classmethod
    def same_everywhere(cls, url: str, **kw: Any) -> "Case":
        """A page with no object: each language keeps the page's own path, no notice."""
        return cls(url, {code: (url, False) for code in LANGUAGES}, **kw)


# --- Reading the switcher like a browser


def _soup(response: Any) -> BeautifulSoup:
    return BeautifulSoup(response.content.decode(), "html.parser")


def switcher_forms(response: Any) -> dict[str, dict[str, str]]:
    """{language: form fields} of the page's switcher: action, hidden inputs and the button."""
    nav = _soup(response).find("nav", class_="language-switcher")
    forms: dict[str, dict[str, str]] = {}
    if nav is None:
        return forms
    for form in nav.find_all("form"):
        button = form.find("button", attrs={"name": "language"})
        assert button is not None, "a switcher form without its language button"
        fields = {
            str(i["name"]): str(i.get("value", "")) for i in form.find_all("input") if i.get("name")
        }
        fields["language"] = str(button["value"])
        fields["_action"] = str(form["action"])
        fields["_current"] = "1" if button.has_attr("disabled") else "0"
        assert str(form["method"]).lower() == "post"
        forms[fields["language"]] = fields
    return forms


def html_lang(response: Any) -> str:
    html = _soup(response).find("html")
    assert html is not None
    return str(html.get("lang"))


def leaks(response: Any, secret: str) -> bool:
    """Whether the secret is in the page's HTML. The one place it may be is the hidden `token`
    field of the action-link confirmation form (the POST body that redeems it): never a URL,
    the switcher or a `next` value."""
    soup = _soup(response)
    for field_ in soup.find_all("input", attrs={"name": "token", "value": secret}):
        field_.decompose()
    return secret in str(soup)


def landing_path(response: Any, started: str) -> str:
    chain = getattr(response, "redirect_chain", [])
    return urlsplit(chain[-1][0] if chain else started).path


def notice_text(code: str) -> str:
    with translation.override(code):
        return gettext(NOTICE_MSGID)


def client_for(case: Case) -> Client:
    client = Client(enforce_csrf_checks=True)  # the real CSRF check, as in a browser
    if case.user is not None:
        if case.mfa:
            verified_login(client, case.user)
        else:
            client.force_login(case.user)
    return client


# --- The sweep itself


def sweep(case: Case, language: str) -> None:
    client = client_for(case)
    page = client.get(case.url, follow=True)
    assert page.status_code == case.status, (case.url, page.status_code)
    forms = switcher_forms(page)
    if case.no_switcher:
        assert forms == {}, f"{case.url}: this page must not have a language switcher"
        return
    assert set(forms) == set(LANGUAGES), f"{case.url}: switcher languages {sorted(forms)}"
    assert forms[html_lang(page)]["_current"] == "1", "the current language is not marked"
    if case.secret:
        assert not leaks(page, case.secret), "a secret is written into the page"
    if forms[language]["_current"] == "1":
        pytest.skip("the current language's button is disabled: nothing to switch")

    data = {k: v for k, v in forms[language].items() if not k.startswith("_")}
    response = client.post(forms[language]["_action"], data, follow=True)
    path, notice = case.expect[language]
    assert response.status_code == case.landing_status, (case.url, language, response.status_code)
    assert landing_path(response, forms[language]["_action"]) == path, (
        f"{case.url} -> {language}: lands on {landing_path(response, '')}, expected {path}"
    )
    assert html_lang(response) == language, f"{case.url} -> {language}: <html lang> is wrong"
    html = response.content.decode()
    assert (notice_text(language) in html) is notice, f"{case.url} -> {language}: notice {notice}"
    if case.secret:
        assert not leaks(response, case.secret), (
            "a secret is written into the page after the switch"
        )
    if notice:  # shown once
        again = client.get(path)
        assert notice_text(language) not in again.content.decode()


# --- Building the route classes. Each builder returns a Case.


def _locale(code: str) -> Locale:
    return Locale.objects.get(language_code=code)


def _translated_url(page: Page, code: str) -> str:
    return str(page.get_translation(_locale(code)).url)


def _translate(page: Page, *codes: str) -> None:
    translate_page(page, codes)


def _expect_pages(page: Page, translated: dict[str, str]) -> dict[str, tuple[str, bool]]:
    """Translation URL where `translated` has the language, else the home with a notice."""
    return {
        code: (translated[code], False) if code in translated else (home_of(code), True)
        for code in LANGUAGES
    }


def home_en() -> Case:
    seed_site()
    return Case("/", {code: (home_of(code), False) for code in LANGUAGES})


def home_it() -> Case:
    seed_site()
    return Case("/it/", {code: (home_of(code), False) for code in LANGUAGES})


def _about(start: str) -> Case:
    seed_site()
    slugs = {code: SEED_TEXTS[code].about.slug for code in LANGUAGES}
    paths = {code: f"{'' if code == 'en' else '/' + code}/{slugs[code]}/" for code in LANGUAGES}
    return Case(paths[start], {code: (paths[code], False) for code in LANGUAGES})


def about_en() -> Case:
    return _about("en")


def about_de() -> Case:
    return _about("de")


def legal_en() -> Case:
    seed_site(legal_placeholders=True)
    paths = {
        code: f"{'' if code == 'en' else '/' + code}/{LEGAL_PLACEHOLDER_TITLES[code]['terms'][1]}/"
        for code in LANGUAGES
    }
    return Case(paths["en"], {code: (paths[code], False) for code in LANGUAGES})


def standard_page_untranslated() -> Case:
    seed_site()
    page = StandardPageFactory.create(title="Only in English")
    page.save_revision().publish()
    return Case(page.url, _expect_pages(page, {"en": page.url}))


def standard_page_partly_translated() -> Case:
    seed_site()
    page = StandardPageFactory.create(title="Half translated")
    page.save_revision().publish()
    _translate(page, "it")
    return Case(page.url, _expect_pages(page, {"en": page.url, "it": _translated_url(page, "it")}))


def legal_page_untranslated() -> Case:
    seed_site()
    page = LegalPageFactory.create(title="Cookie policy", slug="cookie-policy")
    page.save_revision().publish()
    return Case(page.url, _expect_pages(page, {"en": page.url}))


def blog_index_translated() -> Case:
    seed_site()
    index = BlogIndexPageFactory.create()
    _translate(index, "it", "fr")
    found = {
        "en": index.url,
        "it": _translated_url(index, "it"),
        "fr": _translated_url(index, "fr"),
    }
    return Case(index.url, _expect_pages(index, found))


def blog_post_translated() -> Case:
    seed_site()
    index = BlogIndexPageFactory.create()
    post = BlogPostPageFactory.create(parent=index, title="Workshop news")
    post.save_revision().publish()
    _translate(index, "it")
    _translate(post, "it")
    found = {"en": post.url, "it": _translated_url(post, "it")}
    return Case(post.url, _expect_pages(post, found))


def blog_post_untranslated() -> Case:
    seed_site()
    post = BlogPostPageFactory.create(title="Only English news")
    post.save_revision().publish()
    return Case(post.url, _expect_pages(post, {"en": post.url}))


def event_translated() -> Case:
    seed_site()
    index = EventIndexPage.objects.get(locale__language_code="it")
    page = EventPageFactory.create(parent=index, title="Fiera", slug="fiera")
    page.save_revision().publish()
    page.refresh_from_db()
    _translate(page, "en")
    found = {"it": page.url, "en": _translated_url(page, "en")}
    return Case(page.url, _expect_pages(page, found))


def event_untranslated() -> Case:
    seed_site()
    index = EventIndexPage.objects.get(locale__language_code="it")
    page = EventPageFactory.create(parent=index, title="Solo italiano", slug="solo-italiano")
    page.save_revision().publish()
    page.refresh_from_db()
    return Case(page.url, _expect_pages(page, {"it": page.url}))


def vendor_landing_page() -> Case:
    seed_site()
    page = VendorLandingPageFactory.create(title="Our story")
    page.save_revision().publish()
    _translate(page, "it")
    found = {"en": page.url, "it": _translated_url(page, "it")}
    return Case(page.url, _expect_pages(page, found))


def vendor_landing_page_untranslated() -> Case:
    seed_site()
    page = VendorLandingPageFactory.create(title="Our solo story")
    page.save_revision().publish()
    return Case(page.url, _expect_pages(page, {"en": page.url}))


def sell_with_us_page() -> Case:
    seed_site()
    page = VendorRecruitmentPageFactory.create(title="Sell with us")
    page.save_revision().publish()
    return Case(page.url, _expect_pages(page, {"en": page.url}))


def _listed(**kwargs: Any) -> Any:
    kwargs.setdefault("vendor", VendorFactory.create(status=Vendor.Status.ACTIVE))
    kwargs.setdefault("status", "published")
    return ProductFactory.create(**kwargs)


def term_listing_translated() -> Case:
    taxonomy = TaxonomyFactory.create(code="compat", has_landing=True)
    for lang, slug in (("en", "compatibility"), ("it", "compatibilita")):
        TaxonomyTranslationFactory.create(
            taxonomy=taxonomy, language=lang, slug=slug, status=PUBLISHED
        )
    term = TermFactory.create(taxonomy=taxonomy)
    for lang, slug in (("en", "maker"), ("it", "marca")):
        TermTranslationFactory.create(term=term, language=lang, slug=slug, status=PUBLISHED)
    _listed().terms.add(term)
    found = {"en": "/compatibility/maker/", "it": "/it/compatibilita/marca/"}
    return Case("/compatibility/maker/", _expect_pages(None, found))


def term_listing_in_italian() -> Case:
    case = term_listing_translated()
    return Case("/it/compatibilita/marca/", case.expect)


def term_listing_untranslated() -> Case:
    taxonomy = TaxonomyFactory.create(code="compat", has_landing=True)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", slug="compatibility", status=PUBLISHED
    )
    term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=term, language="en", slug="maker", status=PUBLISHED)
    _listed().terms.add(term)
    return Case(
        "/compatibility/maker/",
        _expect_pages(None, {"en": "/compatibility/maker/"}),
    )


def brand_listing() -> Case:
    brand = BrandFactory.create()
    BrandTranslationFactory.create(brand=brand, slug="acme", status=PUBLISHED)
    BrandTranslationFactory.create(brand=brand, language="it", slug="acme-it", status=PUBLISHED)
    _listed(brand=brand)
    found = {"en": "/brand/acme/", "it": "/it/marchio/acme-it/"}
    return Case("/brand/acme/", _expect_pages(None, found))


def category_listing() -> Case:
    parent = CategoryFactory.create()
    CategoryTranslationFactory.create(category=parent, slug="bags", status=PUBLISHED)
    CategoryTranslationFactory.create(
        category=parent, language="it", slug="borse", status=PUBLISHED
    )
    _listed(category=parent)
    found = {"en": "/product-category/bags/", "it": "/it/categoria-prodotto/borse/"}
    return Case("/product-category/bags/", _expect_pages(None, found))


def category_listing_untranslated() -> Case:
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(category=category, slug="hats", status=PUBLISHED)
    _listed(category=category)
    found = {"en": "/product-category/hats/"}
    return Case("/product-category/hats/", _expect_pages(None, found))


def vendor_storefront() -> Case:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(vendor=vendor, language="en", status=PUBLISHED)
    VendorTranslationFactory.create(vendor=vendor, language="it", status=PUBLISHED)
    _listed(vendor=vendor)
    en = f"/shop/{vendor.slug}/"
    return Case(en, _expect_pages(None, {"en": en, "it": f"/it/shop/{vendor.slug}/"}))


def vendor_storefront_untranslated() -> Case:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _listed(vendor=vendor)
    en = f"/shop/{vendor.slug}/"
    return Case(en, _expect_pages(None, {"en": en}))


def not_found() -> Case:
    """A 404 is a page with no object: the switcher keeps the path in the other language (a 404
    as well, so the page after the switch answers 404 too)."""
    seed_site()
    expect = {
        code: (f"{home_of(code)}no-such-page/".replace("//", "/"), False) for code in LANGUAGES
    }
    return Case("/no-such-page/", expect, status=404, landing_status=404)


# Secret-bearing pages: the switch goes to the target home, no notice, the key never echoed.


def _secret(url: Callable[[str], str], status: int = 200) -> Callable[[], Case]:
    def build() -> Case:
        seed_site()
        key = secrets.token_hex(16)
        expect = {code: (home_of(code), False) for code in LANGUAGES}
        return Case(url(key), expect, secret=key, status=status)

    return build


# Unprefixed areas. A signed-in user's choice must hold on them (lane-b/i18n-manager-language).


def sell_with_us_form() -> Case:
    seed_site()
    return Case.same_everywhere("/vendor/apply/")


def sign_in_page() -> Case:
    seed_site()
    return Case.same_everywhere(reverse("login"))


def mfa_overview() -> Case:
    seed_site()
    return Case.same_everywhere("/account/mfa/", user=UserFactory.create())


def manager_shop() -> Case:
    seed_site()
    owner = VendorMemberFactory.create(role="owner").user
    return Case.same_everywhere("/manager/shop/", user=owner, mfa=True)


def manager_platform() -> Case:
    seed_site()
    staff = UserFactory.create(permissions=["core.platform_owner"])
    return Case.same_everywhere("/manager/platform/", user=staff, mfa=True)


def django_admin() -> Case:
    seed_site()
    user = UserFactory.create(superuser=True)
    return Case("/django-admin/", user=user, mfa=True, no_switcher=True)


def cms_admin() -> Case:
    seed_site()
    user = UserFactory.create(superuser=True)
    return Case("/admin/", user=user, mfa=True, no_switcher=True)


def _xfail(reason: str) -> Any:
    return pytest.mark.xfail(strict=True, reason=reason)


# name -> (builder, marks). The names are the route classes of the report.
CASES: dict[str, tuple[Callable[[], Case], list[Any]]] = {
    "cms-home-en": (home_en, []),
    "cms-home-it": (home_it, []),
    "cms-page-translated-en": (about_en, []),
    "cms-page-translated-de": (about_de, []),
    "cms-page-untranslated": (standard_page_untranslated, []),
    "cms-page-partly-translated": (standard_page_partly_translated, []),
    "legal-page-seeded": (legal_en, []),
    "legal-page-untranslated": (legal_page_untranslated, []),
    "blog-index": (blog_index_translated, []),
    "blog-post-translated": (blog_post_translated, []),
    "blog-post-untranslated": (blog_post_untranslated, []),
    "event-translated": (event_translated, []),
    "event-untranslated": (event_untranslated, []),
    "vendor-landing-page": (vendor_landing_page, []),
    "vendor-landing-page-untranslated": (vendor_landing_page_untranslated, []),
    "sell-with-us-cms-page": (sell_with_us_page, []),
    "term-listing-translated": (term_listing_translated, []),
    "term-listing-it": (term_listing_in_italian, []),
    "term-listing-untranslated": (term_listing_untranslated, []),
    "brand-listing": (brand_listing, []),
    "category-listing": (category_listing, []),
    "category-listing-untranslated": (category_listing_untranslated, []),
    "vendor-storefront": (vendor_storefront, []),
    "vendor-storefront-untranslated": (vendor_storefront_untranslated, []),
    "error-404-keeps-its-path": (not_found, []),
    "secret-vendor-apply-cancel": (_secret(lambda k: f"/vendor/apply/cancel/{k}/"), []),
    "secret-vendor-apply-confirm": (_secret(lambda k: f"/vendor/apply/confirm/{k}/", 403), []),
    "secret-vendor-claim": (_secret(lambda k: f"/vendor/claim/{k}/", 403), []),
    "secret-action-link": (_secret(lambda k: f"/account/link/{k}/"), []),
    "unprefixed-vendor-apply-anonymous": (sell_with_us_form, [_xfail(ANONYMOUS_UNPREFIXED)]),
    "unprefixed-sign-in-anonymous": (sign_in_page, [_xfail(ANONYMOUS_UNPREFIXED)]),
    "unprefixed-mfa-signed-in": (mfa_overview, []),
    "unprefixed-manager-shop": (manager_shop, []),
    "unprefixed-manager-platform": (manager_platform, []),
    "admin-django-no-switcher": (django_admin, []),
    "admin-cms-no-switcher": (cms_admin, []),
}

PARAMS = [
    pytest.param(name, code, id=f"{name}-{code}", marks=marks)
    for name, (_builder, marks) in CASES.items()
    for code in LANGUAGES
]


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


@pytest.mark.parametrize(("name", "language"), PARAMS)
def test_the_switcher_does_what_it_was_built_for(name: str, language: str) -> None:
    builder, _marks = CASES[name]
    sweep(builder(), language)


def test_every_route_class_of_the_sweep_is_registered() -> None:
    """Guard against a silently emptied sweep."""
    assert len(CASES) >= 35
    assert re.fullmatch(r"[a-z]{2}", LANGUAGES[0])
