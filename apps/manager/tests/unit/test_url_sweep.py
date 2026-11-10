"""CORE.17 done-when: a sweep fails when a URL under `/manager/` is not under exactly one gated
prefix, so a new view cannot be added ungated (spec 02 §Backoffice, "Tested once per space plus a
sweep"). The sweep also wants every Manager view to declare a registered section of its space."""

from typing import ClassVar

from django.http import HttpRequest, HttpResponse
from django.urls import include, path, re_path
from django.views import View

from apps.manager.checks import manager_route_problems
from apps.manager.spaces import Space


def _plain(request: HttpRequest) -> HttpResponse:
    return HttpResponse("ungated")


class _ShopDashboard(View):
    section: ClassVar[str] = "shop.dashboard"


class _PlatformDashboardOnShop(View):
    section: ClassVar[str] = "platform.dashboard"


class _UnknownSection(View):
    section: ClassVar[str] = "shop.does-not-exist"


def test_the_project_urls_under_the_manager_are_all_gated() -> None:
    assert manager_route_problems() == []


def test_the_project_has_routes_in_both_spaces() -> None:
    from apps.manager.checks import manager_routes

    spaces = {space for _, space in manager_routes()}
    assert spaces == {Space.SHOP, Space.PLATFORM}


def test_a_route_outside_the_gated_prefixes_is_reported() -> None:
    urls = [path("manager/reports/", _plain)]
    problems = manager_route_problems(urls)
    assert len(problems) == 1
    assert "manager/reports/" in problems[0]
    assert "gated prefix" in problems[0]


def test_a_route_at_the_manager_root_is_reported() -> None:
    assert manager_route_problems([path("manager/", _plain)])


def test_a_route_included_under_the_manager_is_reported() -> None:
    urls = [path("manager/", include([path("extra/", _plain)]))]
    problems = manager_route_problems(urls)
    assert problems
    assert "manager/extra/" in problems[0]


def test_a_regex_route_that_may_reach_the_manager_is_reported() -> None:
    assert manager_route_problems([re_path(r"^manager/(shop|platform)/x/$", _plain)])


def test_a_gated_route_without_a_section_is_reported() -> None:
    problems = manager_route_problems([path("manager/shop/plain/", _plain)])
    assert problems
    assert "section" in problems[0]


def test_a_gated_route_whose_section_belongs_to_the_other_space_is_reported() -> None:
    urls = [path("manager/shop/wrong/", _PlatformDashboardOnShop.as_view())]
    problems = manager_route_problems(urls)
    assert problems
    assert "platform" in problems[0]


def test_a_gated_route_with_an_unregistered_section_is_reported() -> None:
    urls = [path("manager/shop/unknown/", _UnknownSection.as_view())]
    assert manager_route_problems(urls)


def test_a_gated_route_with_its_space_section_passes() -> None:
    urls = [path("manager/shop/fine/", _ShopDashboard.as_view())]
    assert manager_route_problems(urls) == []


def test_routes_outside_the_manager_are_not_its_business() -> None:
    assert manager_route_problems([path("shop/", _plain), path("managers/", _plain)]) == []
