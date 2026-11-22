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
from pathlib import Path

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

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
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory
from tests.e2e.axe import format_violations, run_axe
from tests.i18n import category_url, language_prefix, localized

pytestmark = pytest.mark.django_db(transaction=True)

REPORT_DIR = Path(__file__).resolve().parents[2] / "build" / "e2e" / "axe"
# The key public pages: the seeded site (home, About, blog), the recruitment funnel, the legal
# pages, the Manager sign-in, a vendor storefront and a public catalogue listing, in the primary
# and a second language. Cart, checkout and the product page do not exist yet (see Follow-ups).
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
