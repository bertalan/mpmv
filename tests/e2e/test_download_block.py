"""E2E (CMS.15): a page with a download block is axe `wcag22aa` and CSP clean under both themes at
360, 768 and 1280 px, and the link's accessible name holds the title, type and size."""

import pytest
from django.core.files.base import ContentFile
from django.utils import formats
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer
from wagtail.documents import get_document_model
from wagtail.models import Collection

from apps.cms.models import HomePage
from apps.core.i18n import primary_language
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def _download_home(live_server: LiveServer, _cms_site: None) -> None:
    if not Collection.objects.exists():  # a transactional test starts without the root collection
        Collection.add_root(name="Root")
    doc = get_document_model()(title="Terms of sale")
    doc.file.save("terms.pdf", ContentFile(b"%PDF-1.4\n" + b"0" * 1_199_991), save=False)
    doc.save()
    home = HomePage.objects.get(locale__language_code=primary_language())
    home.body = [("download", {"document": doc, "title": ""})]
    home.save_revision().publish()


@pytest.mark.usefixtures("_download_home")
@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_download_block_is_accessible_and_names_file_type_and_size(
    theme: str, width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    page.set_viewport_size({"width": width, "height": 900})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("load")
    point = formats.get_format("DECIMAL_SEPARATOR", lang=primary_language())  # "1,2" in Italian
    link = page.get_by_role("link", name=f"Terms of sale (PDF, 1{point}2 MB)")
    expect(link).to_have_count(1)
    expect(link).to_be_visible()
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
