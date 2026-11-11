"""The SEO settings panel in the Wagtail admin (SEO.8 2/2): one view, in the Settings menu.

A thin adapter over `services.set_site_indexing` (platform staff only, audited). The template
carries no inline script or style (CSP).
"""

from typing import Any

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from wagtail.models import Site

from apps.core.audit import audit_context
from apps.core.indexing import is_noindex_forced

from . import services
from .models import SeoSettings


class SeoSettingsForm(forms.Form):
    site = forms.ModelChoiceField(label=gettext_lazy("Site"), queryset=Site.objects.all())
    allow_indexing = forms.BooleanField(
        label=gettext_lazy("Allow search engines to index this site"),
        required=False,
        help_text=gettext_lazy(
            "Off keeps every page noindex (meta tag, X-Robots-Tag header and robots.txt). "
            "A new site starts off. Turn it on only when the shop is open to the public."
        ),
    )

    def __init__(self, *args: Any, floor: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if floor:
            # Read-only under the environment floor: the posted value is ignored.
            self.fields["allow_indexing"].disabled = True


def _int(value: str | None) -> int | None:
    try:
        return int(value or "")
    except ValueError:
        return None


def seo_settings(request: HttpRequest) -> HttpResponse:
    """Show and change a site's indexing switch (`set_site_indexing`)."""
    if not services.may_edit_seo_settings(request.user):
        raise PermissionDenied
    floor = is_noindex_forced()
    if request.method == "POST":
        form = SeoSettingsForm(request.POST, floor=floor)
        if form.is_valid():
            site = form.cleaned_data["site"]
            if floor:
                form.add_error(None, services.FLOOR_MESSAGE)
            else:
                try:
                    services.set_site_indexing(
                        request.user,
                        site,
                        form.cleaned_data["allow_indexing"],
                        ctx=audit_context(request),
                    )
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    messages.success(request, _("SEO settings saved."))
                    return redirect(reverse("seo_admin:settings") + f"?site={site.pk}")
    else:
        site = (
            Site.objects.filter(pk=_int(request.GET.get("site"))).first()
            or Site.objects.filter(is_default_site=True).first()
        )
        initial: dict[str, Any] = {}
        if site is not None:
            initial = {
                "site": site.pk,
                "allow_indexing": SeoSettings.current(site).allow_indexing,
            }
        form = SeoSettingsForm(initial=initial, floor=floor)
    return TemplateResponse(
        request,
        "seo/admin/settings.html",
        {
            "page_title": _("SEO settings"),
            "header_icon": "search",
            "form": form,
            "floor": floor,
            "submit_label": _("Save"),
        },
    )
