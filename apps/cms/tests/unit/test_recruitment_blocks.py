"""CMS.5: the recruitment page's plan and FAQ blocks, and the link to the VEN.22 application.

Until VEN.22 ships its public form (URL name `vendors:apply`), the page has no application URL
and shows a fallback instead of a broken link.
"""

import pytest
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.urls import include, path

from apps.cms.models import FAQItemBlock, PlanBlock
from apps.cms.services import APPLICATION_URL_NAME, vendor_application_url


def _apply_view(request: HttpRequest) -> HttpResponse:  # pragma: no cover - only reversed
    return HttpResponse()


# A stand-in URLconf with the planned VEN.22 route, so the link is tested before VEN.22 exists.
_vendor_patterns = ([path("apply/", _apply_view, name="apply")], "vendors")
urlpatterns = [path("sell/", include(_vendor_patterns))]


def test_a_plan_needs_a_name() -> None:
    block = PlanBlock()
    with pytest.raises(ValidationError):
        block.clean(block.to_python({"name": "", "price": "Free", "features": []}))


def test_a_plan_with_name_price_and_features_is_valid() -> None:
    block = PlanBlock()
    value = block.clean(
        block.to_python(
            {"name": "Starter", "price": "Free", "summary": "", "features": ["50 products"]}
        )
    )
    assert value["name"] == "Starter"
    assert list(value["features"]) == ["50 products"]


def test_a_faq_item_needs_a_question_and_an_answer() -> None:
    block = FAQItemBlock()
    with pytest.raises(ValidationError):
        block.clean(block.to_python({"question": "Who can sell?", "answer": ""}))


def test_the_planned_application_url_name_is_the_vendors_apply_route() -> None:
    assert APPLICATION_URL_NAME == "vendors:apply"


def test_the_application_url_comes_from_the_ven22_route_when_it_exists() -> None:
    assert vendor_application_url(urlconf=__name__) == "/sell/apply/"


def test_without_the_ven22_route_there_is_no_application_url() -> None:
    assert vendor_application_url(urlconf="apps.cms.urls") is None
