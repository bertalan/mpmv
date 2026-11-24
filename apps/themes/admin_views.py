"""The palette builder in the Wagtail admin (THM.34, spec 14 §14.12): one page in the Settings
menu, a thin adapter over `builder.build_report`, `services.save_palette` and
`services.set_site_theme`.

It is a plain form that works without JavaScript: "Check" measures every pair, a failing pair
carries a "Use this" button that submits the nearest compliant shade, and "Save" is refused with
the pair and its ratio while one fails. The server derives every role from the three base colours
itself, so a posted palette is never trusted. No inline script or style (CSP).
"""

import re
from typing import Any

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from wagtail.models import Site

from apps.core.audit import audit_context
from apps.core.nfc_forms import NFCFormMixin

from .builder import BASE_ROLES, BuilderReport, build_report, preview_css
from .engine import ThemeError
from .models import BrandPalette
from .palettes import DEFAULT_PALETTE, PaletteRejected
from .services import (
    registered_surfaces,
    save_palette,
    set_site_theme,
    site_theme_choice,
)

PERMISSION = "themes.change_themesettings"


def may_use_palette_builder(user: Any) -> bool:
    """The permission that edits a site's theme settings (the Wagtail settings permission)."""
    return bool(user.is_authenticated and user.is_active and user.has_perm(PERMISSION))


def _hex_field(label: Any, *, required: bool) -> forms.RegexField:
    return forms.RegexField(
        regex=r"^#[0-9A-Fa-f]{6}$",
        max_length=7,
        required=required,
        label=label,
        error_messages={"invalid": gettext_lazy("Use a six digit hex colour such as #C40417.")},
    )


class PaletteBuilderForm(NFCFormMixin, forms.Form):
    # Rendered as a hidden field: the "Start from" choice is a separate GET form.
    start = forms.ModelChoiceField(
        label=gettext_lazy("Start from"),
        queryset=BrandPalette.objects.all(),
        to_field_name="slug",
        empty_label=None,
    )
    name = forms.CharField(label=gettext_lazy("Palette name"), max_length=64)
    primary = _hex_field(gettext_lazy("Primary"), required=True)
    secondary = _hex_field(gettext_lazy("Secondary (accent)"), required=False)
    band = _hex_field(gettext_lazy("Band"), required=False)
    site = forms.ModelChoiceField(
        label=gettext_lazy("Use on this site"),
        queryset=Site.objects.all(),
        required=False,
        empty_label=gettext_lazy("Do not apply to a site"),
        help_text=gettext_lazy("Choose a site to switch it to this palette when you save."),
    )


def _start_palette(request: HttpRequest) -> BrandPalette | None:
    wanted = request.POST.get("start") or request.GET.get("start") or DEFAULT_PALETTE
    return (
        BrandPalette.objects.filter(slug=wanted).first()
        or BrandPalette.objects.filter(slug=DEFAULT_PALETTE).first()
        or BrandPalette.objects.first()
    )


def _initial(start: BrandPalette) -> dict[str, Any]:
    return {
        "start": start.slug,
        "name": "",
        "primary": start.primary,
        "secondary": start.secondary,
        "band": start.band,
    }


def _render(
    request: HttpRequest,
    form: PaletteBuilderForm,
    report: BuilderReport | None,
    errors: list[str],
) -> HttpResponse:
    failing = len(report.failing) if report is not None else 0
    return TemplateResponse(
        request,
        "themes/admin/palette_builder.html",
        {
            "page_title": _("Palette builder"),
            "header_icon": "palette",
            "form": form,
            "report": report,
            "failing": failing,
            "errors": errors,
            "base_roles": BASE_ROLES,
            "check_url": reverse("themes_admin:palette_check"),
            "preview_url": reverse("themes_admin:palette_preview"),
        },
    )


def palette_builder(request: HttpRequest) -> HttpResponse:
    """Build, check and save a site palette. Staff holding the theme settings permission only."""
    if not may_use_palette_builder(request.user):
        raise PermissionDenied
    start = _start_palette(request)
    if start is None:
        return _render(request, PaletteBuilderForm(), None, [_("No palette to start from.")])
    surfaces = registered_surfaces()
    if request.method != "POST":
        form = PaletteBuilderForm(initial=_initial(start))
        return _render(request, form, build_report(start.roles(), start.roles(), surfaces), [])

    data = request.POST.copy()
    action = data.get("action", "check")
    use = request.POST.get("use", "")
    if use:  # "Use this": the button's value is `<field>:<colour>`
        action = "check"
        field, _sep, colour = use.partition(":")
        if field in BASE_ROLES:
            data[field] = colour
    form = PaletteBuilderForm(data)
    if not form.is_valid():
        return _render(request, form, None, [])
    base = {role: form.cleaned_data[role] for role in BASE_ROLES}
    try:
        report = build_report(base, start.roles(), surfaces)
    except ThemeError as exc:
        return _render(request, form, None, [str(exc)])
    if action != "save":
        return _render(request, form, report, [])
    return _save(request, form, report, start)


def _save(
    request: HttpRequest, form: PaletteBuilderForm, report: BuilderReport, start: BrandPalette
) -> HttpResponse:
    name = form.cleaned_data["name"].strip()
    slug = slugify(name)[:32]
    if not slug:
        form.add_error("name", _("Use letters or digits in the name."))
        return _render(request, form, report, [])
    ctx = audit_context(request)
    try:
        save_palette(slug, name, report.palette, ctx=ctx)
        site = form.cleaned_data["site"]
        if site is not None:
            row = BrandPalette.objects.get(slug=slug)
            set_site_theme(site, site_theme_choice(site), palette=row, ctx=ctx)
    except PaletteRejected as exc:
        return _render(request, form, report, [str(v) for v in exc.violations])
    except ThemeError as exc:
        return _render(request, form, report, [str(exc)])
    messages.success(request, _("Palette %(name)s saved.") % {"name": name})
    return redirect(reverse("themes_admin:palette_builder") + f"?start={slug}")


# --- The live parts (THM.34 2/2): the script asks the server, so the numbers it shows are the
# ones `check_brand_palette` enforces on save.

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _live_report(request: HttpRequest) -> BuilderReport | None:
    """The report of the base colours in the query string; None when one is not a hex colour."""
    start = _start_palette(request)
    if start is None:
        return None
    base = {role: request.GET.get(role, "") for role in BASE_ROLES}
    if any(value and not _HEX.match(value) for value in base.values()):
        return None
    try:
        return build_report(base, start.roles(), registered_surfaces())
    except ThemeError:
        return None


@require_GET
@never_cache
def palette_check(request: HttpRequest) -> HttpResponse:
    """Every pair with its ratio and the nearest compliant shade, as JSON. Writes nothing."""
    if not may_use_palette_builder(request.user):
        raise PermissionDenied
    report = _live_report(request)
    if report is None:
        return JsonResponse({"error": _("Use a six digit hex colour such as #C40417.")}, status=400)
    rows = [
        {
            "name": f"{row.result.name} ({row.result.foreground} / {row.result.background})",
            "ratio": f"{row.result.ratio:.2f}",
            "minimum": f"{row.result.minimum:g}",
            "passes": row.result.passes,
            "label": _("Pass") if row.result.passes else _("Fail"),
            "suggestion": (
                {
                    "field": row.suggestion.field,
                    "colour": row.suggestion.colour,
                    "label": _("Use this: %(colour)s for %(field)s")
                    % {"colour": row.suggestion.colour, "field": row.suggestion.field},
                }
                if row.suggestion
                else None
            ),
            "no_fix": (
                ""
                if row.result.passes or row.suggestion
                else _("No base colour to change for this pair.")
            ),
        }
        for row in report.rows
    ]
    failing = len(report.failing)
    summary = (
        ngettext("%(counter)s pair fails.", "%(counter)s pairs fail.", failing)
        % {"counter": failing}
        if failing
        else _("Every pair passes.")
    )
    return JsonResponse(
        {
            "rows": rows,
            "failing": failing,
            "valid": report.valid,
            "summary": summary,
            "reason": _(
                "Save is off while a pair fails. Use the suggested shade or pick another colour, "
                "then check again."
            )
            if failing
            else "",
        }
    )


@require_GET
@never_cache
def palette_preview(request: HttpRequest) -> HttpResponse:
    """The preview stylesheet of a candidate palette: generated, same-origin, scoped, uncached."""
    if not may_use_palette_builder(request.user):
        raise PermissionDenied
    report = _live_report(request)
    if report is None:
        return HttpResponse(status=400)
    return HttpResponse(preview_css(report.palette), content_type="text/css; charset=utf-8")
