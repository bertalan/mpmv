"""SEC.13 done-when: an anonymous POST view with no protection fails the sweep; each of the three
protections passes; a signed token or an allow-list entry without a rate limit fails; the register
`docs/dev/public-forms.md` is fresh and lists every declared route."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, get_resolver, path
from django.views import View
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import checks as core_checks
from apps.core import public_forms
from apps.core.public_forms import (
    REGISTER_PATH,
    FormSpec,
    Kind,
    Protection,
    declare_public_form,
    not_anonymous,
    pre_mfa_step,
    public_form,
    public_form_report,
    render_register,
)

ROOT = Path(__file__).resolve().parents[4]
WITH_WIDGET = "manager/login.html"
WITHOUT_WIDGET = "vendors/apply/sent.html"


def _ok(request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok")


def _fresh() -> Callable[..., HttpResponse]:
    """A new POST view each time: a declaration is set on the view, so tests must not share one."""

    @require_POST
    def unprotected(request: HttpRequest) -> HttpResponse:
        return _ok(request)

    return unprotected


class UnprotectedClass(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        return _ok(request)


@require_http_methods(["GET", "HEAD"])
def read_only(request: HttpRequest) -> HttpResponse:
    return _ok(request)


def reads_post(request: HttpRequest) -> HttpResponse:
    return HttpResponse(request.POST.get("x", ""))


def _errors(*patterns: URLPattern | URLResolver) -> list[str]:
    report = public_form_report(list(patterns))
    return [f"{code} {text}" for code, text in report.errors]


def _decorated(**kwargs: Any) -> Callable[..., HttpResponse]:
    return public_form(purpose="test", task="SEC.13", **kwargs)(_fresh())


@pytest.fixture(autouse=True)
def _no_declarations() -> Iterator[None]:
    _ = get_resolver().url_patterns  # import the URLconf first: it declares Wagtail's views
    saved = dict(public_forms._declared)
    yield
    public_forms._declared.clear()
    public_forms._declared.update(saved)


def test_the_project_has_no_undeclared_anonymous_post() -> None:
    assert public_form_report().errors == []


def test_check_on_the_project_has_only_info_for_the_known_gaps() -> None:
    from django.core import checks

    messages = core_checks.check_public_forms()
    assert all(m.level < checks.WARNING for m in messages)  # the deploy gate fails on warnings
    assert {m.id for m in messages} <= {"core.I701"}


def test_an_unprotected_function_view_fails() -> None:
    assert [e[:9] for e in _errors(path("x/", _fresh(), name="x"))] == ["core.E701"]


def test_an_unprotected_class_based_view_fails() -> None:
    assert [e[:9] for e in _errors(path("x/", UnprotectedClass.as_view(), name="x"))] == [
        "core.E701"
    ]


def test_a_view_that_reads_request_post_fails() -> None:
    assert [e[:9] for e in _errors(path("x/", reads_post))] == ["core.E701"]


def test_a_read_only_view_passes() -> None:
    assert _errors(path("x/", read_only), path("y/", View.as_view())) == []


def test_a_gated_prefix_is_not_swept() -> None:
    assert _errors(path("manager/x/", _fresh())) == []


def test_nested_includes_are_walked() -> None:
    from django.urls import include

    nested = [path("a/", include([path("b/", include([path("c/", _fresh())]))]))]
    assert "/a/b/c/" in _errors(*nested)[0]


def test_captcha_with_the_widget_passes() -> None:
    view = _decorated(protection=Protection.CAPTCHA, templates=(WITH_WIDGET,))
    assert _errors(path("x/", view)) == []


@pytest.mark.parametrize(
    "templates",
    [(), (WITHOUT_WIDGET,), ("does/not/exist.html",)],
    ids=["none", "no-widget", "missing"],
)
def test_captcha_without_the_widget_fails(templates: tuple[str, ...]) -> None:
    view = _decorated(protection=Protection.CAPTCHA, templates=templates)
    assert [e[:9] for e in _errors(path("x/", view))] == ["core.E702"]


@pytest.mark.parametrize("protection", [Protection.SIGNED_TOKEN, Protection.ALLOW])
def test_token_and_allow_list_need_a_reason_and_a_rate_limit(protection: Protection) -> None:
    complete = _decorated(protection=protection, reason="why", rate_limit="per IP")
    assert _errors(path("x/", complete)) == []
    no_limit = _decorated(protection=protection, reason="why")
    assert [e[:9] for e in _errors(path("x/", no_limit))] == ["core.E702"]
    no_reason = _decorated(protection=protection, rate_limit="per IP")
    assert [e[:9] for e in _errors(path("x/", no_reason))] == ["core.E702"]


def test_no_rate_limit_needs_its_reason_text_and_passes_when_given() -> None:
    view = _decorated(protection=Protection.ALLOW, reason="why", no_rate_limit="does no work")
    assert _errors(path("x/", view)) == []


def test_a_declaration_survives_other_decorators_and_class_based_views() -> None:
    @public_form(
        protection=Protection.ALLOW, purpose="t", task="SEC.13", reason="r", rate_limit="per IP"
    )
    class Declared(UnprotectedClass):
        pass

    assert (
        _errors(path("x/", Declared.as_view()), path("y/", require_POST(Declared.as_view()))) == []
    )


def test_a_signed_in_view_refusing_anonymous_passes_but_not_when_pre_mfa_exempt() -> None:
    view: Any = not_anonymous(reason="403 for anonymous", purpose="t", task="SEC.13")(_fresh())
    assert _errors(path("x/", view)) == []
    view.mfa_exempt = True
    assert [e[:9] for e in _errors(path("x/", view))] == ["core.E702"]


def test_a_known_gap_is_informational_not_an_error() -> None:
    view = _decorated(protection=Protection.ALLOW, known_gap="no limit yet")
    report = public_form_report([path("x/", view)])
    assert report.errors == []
    assert [code for code, _ in report.warnings] == ["core.I701"]
    assert len(report.public) == 1


def test_a_declaration_for_a_route_that_does_not_exist_fails() -> None:
    declare_public_form(
        "no_such_route", FormSpec(Kind.PUBLIC, "t", "SEC.13", Protection.ALLOW), view="x.y"
    )
    assert any(code == "core.E703" for code, _ in public_form_report().errors)


def test_pre_mfa_steps_have_their_own_table() -> None:
    view = pre_mfa_step(purpose="second factor", task="CORE.4")(_fresh())
    report = public_form_report([path("x/", view, name="step")])
    assert (report.errors, len(report.public), len(report.pre_mfa)) == ([], 0, 1)
    text = render_register(report)
    assert text.index("Anonymous POST routes") < text.index("`step`")  # in the second table
    assert "second factor not verified yet" in text.split("`step`")[0]


def test_the_register_is_fresh_and_lists_every_declared_route() -> None:
    report = public_form_report()
    text = render_register(report)
    assert (ROOT / REGISTER_PATH).read_text() == text, "run `make docs`"
    for row in (*report.public, *report.pre_mfa):
        assert f"`{row.leaf.path}`" in text


# --- Review fixes: pages, settings, writes other than POST, wrappers, regex routes, known gaps


def test_the_project_page_types_read_no_post() -> None:
    assert public_forms.page_problems() == []


def test_a_page_type_that_reads_a_post_fails_unless_declared() -> None:
    class ContactPage:
        __module__ = "apps.demo.models"

        def serve(self, request: HttpRequest) -> HttpResponse:
            return HttpResponse(request.POST.get("message", ""))

    class RoutedPage:
        __module__ = "apps.demo.models"

        def send(self, request: HttpRequest) -> HttpResponse:
            return HttpResponse("sent" if request.method == "POST" else "form")

        send._routablepage_routes = []  # type: ignore[attr-defined]  # as @path() leaves it

    class QuietPage:
        __module__ = "apps.demo.models"

        def serve(self, request: HttpRequest) -> HttpResponse:
            return HttpResponse("page")

    declared = public_form(
        protection=Protection.ALLOW, purpose="t", task="SEC.13", reason="r", rate_limit="per IP"
    )(type("Declared", (ContactPage,), {"__module__": "apps.demo.models"}))
    problems = public_forms.page_problems([ContactPage, RoutedPage, QuietPage, declared])
    assert [p.split(" ")[0].rsplit(">.", 1)[-1] for p in problems] == [
        "ContactPage.serve",
        "RoutedPage.send",
    ]


def test_wagtail_password_reset_and_page_passwords_must_be_declared(settings: Any) -> None:
    settings.WAGTAIL_PASSWORD_RESET_ENABLED = True
    assert any("wagtailadmin_password_reset" in p for p in public_forms.settings_problems())
    declare_public_form(
        "wagtailadmin_password_reset",
        FormSpec(Kind.PUBLIC, "t", "SEC.13", Protection.ALLOW, known_gap="no limit"),
        view="x.y",
    )
    assert not any("password_reset" in p for p in public_forms.settings_problems())
    settings.WAGTAIL_PRIVATE_PAGE_OPTIONS = {"SHARED_PASSWORD": True}
    settings.BLOCK_PAGE_RESTRICTION_ROUTES = False
    assert any("shared password" in p for p in public_forms.settings_problems())
    assert any("_util/" in p for p in public_forms.settings_problems())  # the gate needs the block
    settings.BLOCK_PAGE_RESTRICTION_ROUTES = True  # blocked: the form is unreachable
    assert not any("shared password" in p for p in public_forms.settings_problems())


def test_the_wagtail_restriction_routes_are_gated_not_declared() -> None:
    report = public_form_report()
    assert not [r for r in report.public if "_util" in r.leaf.path]
    assert report.warnings == []  # no known gap is left
    assert report.errors == []


def test_a_known_gap_needs_a_task_id() -> None:
    view = _decorated(protection=Protection.ALLOW, known_gap="no limit yet")
    bad = public_form(
        protection=Protection.ALLOW, purpose="t", task="later", known_gap="no limit yet"
    )(_fresh())
    assert _errors(path("x/", view)) == []
    assert [e[:9] for e in _errors(path("x/", bad))] == ["core.E702"]


def test_the_register_shows_a_known_gap_with_its_task() -> None:
    view = _decorated(protection=Protection.ALLOW, known_gap="no limit yet")
    text = render_register(public_form_report([path("x/", view, name="gap")]))
    assert "NOT PROTECTED (SEC.13): no limit yet" in text


def test_a_declaration_by_name_does_not_apply_to_another_view_with_that_name() -> None:
    declare_public_form(
        "shared_name",
        FormSpec(Kind.PUBLIC, "t", "SEC.13", Protection.ALLOW, reason="r", rate_limit="x"),
        view="elsewhere.view",
    )
    assert [e[:9] for e in _errors(path("x/", _fresh(), name="shared_name"))] == ["core.E701"]


def test_a_regex_route_is_walked() -> None:
    from django.urls import re_path

    assert [e[:9] for e in _errors(re_path(r"^legacy/(?P<n>\d+)/$", _fresh()))] == ["core.E701"]


def test_a_decorator_without_wraps_does_not_hide_the_view() -> None:
    def hiding(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        def inner(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            return view(request, *args, **kwargs)

        return inner

    assert [e[:9] for e in _errors(path("x/", hiding(reads_post)))] == ["core.E701"]


def test_writes_other_than_post_are_found() -> None:
    class DeleteOnly(View):
        http_method_names = ["delete"]

        def delete(self, request: HttpRequest) -> HttpResponse:
            return _ok(request)

    def json_endpoint(request: HttpRequest) -> HttpResponse:
        if request.method != "GET":
            return HttpResponse(request.body)
        return _ok(request)

    for view in (DeleteOnly.as_view(), json_endpoint):
        assert [e[:9] for e in _errors(path("x/", view))] == ["core.E701"]
