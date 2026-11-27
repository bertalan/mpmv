"""E2E (CMP.7): the automated accessibility audit of the key public pages.

Done when (spec 11, CMP.7): axe-core with the `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` and
`wcag22aa` tags (the THM.5 harness in `tests/e2e/axe.py`) runs on the key public pages. A page
with a serious or critical violation fails the build; lesser findings (moderate, minor) are
listed in the report only. Every page writes its findings to `build/e2e/axe/` (a JSON file per
page plus `summary.md`), which CI uploads as the `e2e-axe` artifact. The manual review (focus
not obscured, consistent help, redundant entry, accessible authentication) and the published
statement are separate, human steps.
"""

import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cart.models import Cart
from apps.cart.session import GUEST_CART_SESSION_KEY
from apps.cart.tests.factories import CartFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
)
from apps.cms.tests.factories import (
    BlogIndexPageFactory,
    LegalPageFactory,
    VendorRecruitmentPageFactory,
)
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import TranslationStatus
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders.tests.integration.test_checkout_views import add, vendor
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import (
    CommissionRuleFactory,
    VendorFactory,
    VendorTranslationFactory,
)
from tests.e2e.axe import format_violations, run_axe
from tests.i18n import category_url, language_prefix, localized, translated

pytestmark = pytest.mark.django_db(transaction=True)

REPORT_DIR = Path(__file__).resolve().parents[2] / "build" / "e2e" / "axe"
# The key public pages: the seeded site (home, About, blog), the recruitment funnel, the legal
# pages, the Manager sign-in, a vendor storefront and a public catalogue listing, in the primary
# and a second language. The product page, cart and checkout steps have their own test below.
PRIMARY = primary_language()
SECOND = next((code for code in configured_languages() if code != PRIMARY), PRIMARY)
KEY_PAGES = tuple(
    dict.fromkeys(
        (
            "/",
            f"{language_prefix(SECOND)}/",
            "/blog/",
            "/vendor/apply/",
            "/terms-of-sale/",
            "/sell-with-us/",
            "/account/login/",
            localized("/shop/acme/"),
            localized("/shop/acme/", SECOND),
            category_url(PRIMARY, "vases"),
            category_url(SECOND, "vasi"),
        )
    )
)


@pytest.fixture
def storefront() -> Vendor:
    """The pages and the vendor the key paths need, on top of the seeded site."""
    blog = BlogIndexPageFactory.create(live=False)
    blog.save_revision().publish()
    terms = LegalPageFactory.create(title="Terms of sale", live=False)
    terms.save_revision().publish()
    VendorRecruitmentPageFactory.create(title="Sell with us")
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, slug="acme", display_name="Acme"
    )
    for language in dict.fromkeys((PRIMARY, SECOND)):
        VendorTranslationFactory.create(
            vendor=vendor, language=language, status=TranslationStatus.PUBLISHED
        )
    category = CategoryFactory.create()
    for language, name, slug in ((PRIMARY, "Vases", "vases"), (SECOND, "Vasi", "vasi")):
        CategoryTranslationFactory.create(
            category=category,
            language=language,
            name=name,
            slug=slug,
            status=TranslationStatus.PUBLISHED,
        )
    ProductFactory.create(vendor=vendor, category=category, status=Product.Status.PUBLISHED)
    return vendor


@pytest.fixture(scope="module", autouse=True)
def _fresh_report_dir() -> Iterator[None]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for old in [*REPORT_DIR.glob("*.json"), REPORT_DIR / "summary.md"]:
        old.unlink(missing_ok=True)
    yield
    write_summary()


def slug_of(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "home"


def write_summary() -> None:
    rows = []
    for report in sorted(REPORT_DIR.glob("*.json")):
        data = json.loads(report.read_text(encoding="utf-8"))
        impacts = [v.get("impact") for v in data["violations"]]
        rows.append(
            f"| `{data['path']}` | {impacts.count('critical')} | {impacts.count('serious')} | "
            f"{impacts.count('moderate') + impacts.count('minor')} |"
        )
    if rows:
        header = "| Page | Critical | Serious | Moderate or minor |\n| --- | ---: | ---: | ---: |\n"
        (REPORT_DIR / "summary.md").write_text(
            "## Accessibility audit (axe, WCAG 2.2 AA)\n\n" + header + "\n".join(rows) + "\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize("path", KEY_PAGES)
def test_key_page_has_no_axe_violation(
    path: str, page: Page, live_server: LiveServer, storefront: Vendor
) -> None:
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.status == 200, f"{path} answered {response.status}"
    page.wait_for_load_state("load")

    violations = run_axe(page)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"{slug_of(path)}.json").write_text(
        json.dumps({"path": path, "violations": violations}, indent=2), encoding="utf-8"
    )
    assert violations == [], format_violations(violations)


def record(slug: str, path: str, violations: list[dict[str, object]]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"{slug}.json").write_text(
        json.dumps({"path": path, "violations": violations}, indent=2), encoding="utf-8"
    )


@pytest.fixture
def shop(settings: Any) -> Cart:
    """A guest cart with one item, the tax rates and the default commission rule."""
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=Decimal("22"), valid_from=utc(2026, 1, 1)
    )
    CommissionRuleFactory.create(valid_from=utc(2026, 1, 1), valid_to=None)
    cart = CartFactory.create()
    product = add(cart, vendor("Rossi Ceramics"), "100.00", "Blue vase")
    product.translations.update(slug="blue-vase")  # the page the cart line links to
    return cart


def test_product_cart_and_checkout_pages_have_no_axe_violation(
    page: Page, live_server: LiveServer, shop: Cart
) -> None:
    """The product page, the cart with an item and the checkout details and review steps."""
    store = SessionStore()
    store[GUEST_CART_SESSION_KEY] = shop.session_key
    store.save()
    page.context.add_cookies(
        [{"name": settings.SESSION_COOKIE_NAME, "value": store.session_key or "",
          "url": live_server.url}]
    )  # fmt: skip

    def audit(slug: str) -> None:
        page.wait_for_load_state("load")
        violations = run_axe(page)
        record(slug, slug, violations)
        assert violations == [], f"{slug}: {format_violations(violations)}"

    page.goto(f"{live_server.url}{localized('/product/blue-vase/')}")
    audit("product-detail")
    page.goto(f"{live_server.url}{localized('/cart/')}")
    audit("cart")
    page.goto(f"{live_server.url}{localized('/checkout/')}")
    audit("checkout-details")
    # An empty submit (the form is novalidate) shows the error summary and the field errors.
    page.get_by_role("button", name=translated("Continue to review")).click()
    expect(page.get_by_role("heading", level=1)).to_have_text(translated("Your details"))
    expect(page.locator("[aria-invalid='true']").first).to_be_visible()
    audit("checkout-details-errors")
    page.locator("#id_email").fill("buyer@example.com")
    page.locator("#id_full_name").fill("Mario Rossi")
    page.locator("#id_line1").fill("1 Via Test")
    page.locator("#id_postcode").fill("00100")
    page.locator("#id_city").fill("Roma")
    page.locator("#id_country").select_option("IT")
    page.locator("#id_city").press("Enter")
    expect(page.get_by_role("heading", level=1)).to_have_text(translated("Review and pay"))
    audit("checkout-review")
