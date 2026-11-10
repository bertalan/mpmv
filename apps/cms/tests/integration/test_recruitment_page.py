"""CMS.5: the "Sell with us" page shows the plans and the FAQ and leads to the application.

Done when: "the recruitment page shows the plans and links to the application". The application
form is VEN.22 (URL name `vendors:apply`); until it exists the page shows a notice instead of a
broken link. The page is served under the strict CSP: no inline scripts or styles.
"""

import re

import pytest
from django.test import Client

from apps.cms.models import HomePage, VendorRecruitmentPage
from apps.cms.services import SeedReport, vendor_application_url
from apps.cms.tests.factories import VendorRecruitmentPageFactory

pytestmark = pytest.mark.django_db


def test_the_page_shows_every_plan_with_its_features(client: Client, seeded: SeedReport) -> None:
    VendorRecruitmentPageFactory.create(title="Sell with us")

    response = client.get("/sell-with-us/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "<h1>Sell with us</h1>" in html
    assert "Open your shop." in html
    for text in (
        "Starter",
        "No monthly fee",
        "Up to 50 products",
        "Workshop",
        "Reduced commission",
    ):
        assert text in html
    assert 'class="plan plan--highlighted"' in html


def test_the_page_shows_the_faq(client: Client, seeded: SeedReport) -> None:
    VendorRecruitmentPageFactory.create(title="Sell with us")
    html = client.get("/sell-with-us/").content.decode()
    assert "Who can sell?" in html
    assert "Independent makers." in html


def test_the_page_links_to_the_application_form(client: Client, seeded: SeedReport) -> None:
    assert vendor_application_url() == "/vendor/apply/"  # VEN.22
    VendorRecruitmentPageFactory.create(title="Sell with us", cta_label="Apply now")

    html = client.get("/sell-with-us/").content.decode()
    assert '<a class="button" href="/vendor/apply/">Apply now</a>' in html
    assert "Applications open soon." not in html


@pytest.mark.parametrize(
    ("cta_label", "shown"), [("Open your shop", "Open your shop"), ("", "Apply now")]
)
def test_with_the_application_form_the_page_links_to_it(
    client: Client, seeded: SeedReport, monkeypatch: pytest.MonkeyPatch, cta_label: str, shown: str
) -> None:
    # VEN.22's route stands in through the service the page asks for the URL.
    monkeypatch.setattr("apps.cms.services.vendor_application_url", lambda: "/sell/apply/")
    VendorRecruitmentPageFactory.create(title="Sell with us", cta_label=cta_label)

    html = client.get("/sell-with-us/").content.decode()
    assert f'<a class="button" href="/sell/apply/">{shown}</a>' in html
    assert "Applications open soon." not in html


def test_the_page_has_no_inline_scripts_or_styles(client: Client, seeded: SeedReport) -> None:
    VendorRecruitmentPageFactory.create(title="Sell with us")
    html = client.get("/sell-with-us/").content.decode()
    # The base layout loads same-origin script files (THM.3); none is inline.
    assert not re.search(r"<script\b(?![^>]*\ssrc=)", html)
    assert "<style" not in html
    assert "style=" not in html


def test_the_recruitment_page_goes_under_a_home_page() -> None:
    assert VendorRecruitmentPage.parent_page_types == ["cms.HomePage"]
    assert "cms.VendorRecruitmentPage" in HomePage.subpage_types
    assert "cms.LegalPage" in HomePage.subpage_types
