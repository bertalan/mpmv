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


@require_POST
def unprotected(request: HttpRequest) -> HttpResponse:
    return _ok(request)


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
    return public_form(purpose="test", task="SEC.13", **kwargs)(unprotected)


@pytest.fixture(autouse=True)
def _no_declarations() -> Iterator[None]:
    _ = get_resolver().url_patterns  # import the URLconf first: it declares Wagtail's views
    saved = dict(public_forms._declared)
    yield
    public_forms._declared.clear()
    public_forms._declared.update(saved)


def test_the_project_has_no_undeclared_anonymous_post() -> None:
    assert public_form_report().errors == []


def test_check_on_the_project_has_no_error_only_the_known_gaps() -> None:
    from django.core import checks

    messages = core_checks.check_public_forms()
    assert not [m for m in messages if isinstance(m, checks.Error)]
    assert {m.id for m in messages} <= {"core.W701"}


def test_an_unprotected_function_view_fails() -> None:
    assert [e[:9] for e in _errors(path("x/", unprotected, name="x"))] == ["core.E701"]


def test_an_unprotected_class_based_view_fails() -> None:
    assert [e[:9] for e in _errors(path("x/", UnprotectedClass.as_view(), name="x"))] == [
        "core.E701"
    ]


def test_a_view_that_reads_request_post_fails() -> None:
    assert [e[:9] for e in _errors(path("x/", reads_post))] == ["core.E701"]


def test_a_read_only_view_passes() -> None:
    assert _errors(path("x/", read_only), path("y/", View.as_view())) == []


def test_a_gated_prefix_is_not_swept(settings: object) -> None:
    assert _errors(path("manager/x/", unprotected)) == []


def test_nested_includes_are_walked() -> None:
    from django.urls import include

    nested = [path("a/", include([path("b/", include([path("c/", unprotected)]))]))]
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
    view: Any = not_anonymous(reason="403 for anonymous", purpose="t", task="SEC.13")(unprotected)
    assert _errors(path("x/", view)) == []
    view.mfa_exempt = True
    assert [e[:9] for e in _errors(path("x/", view))] == ["core.E702"]


def test_a_known_gap_is_a_warning_not_an_error() -> None:
    view = _decorated(protection=Protection.ALLOW, known_gap="no limit yet")
    report = public_form_report([path("x/", view)])
    assert report.errors == []
    assert [code for code, _ in report.warnings] == ["core.W701"]
    assert len(report.public) == 1


def test_a_declaration_for_a_route_that_does_not_exist_fails() -> None:
    declare_public_form("no_such_route", FormSpec(Kind.PUBLIC, "t", "SEC.13", Protection.ALLOW))
    assert any(code == "core.E703" for code, _ in public_form_report().errors)


def test_pre_mfa_steps_have_their_own_table() -> None:
    view = pre_mfa_step(purpose="second factor", task="CORE.4")(unprotected)
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
