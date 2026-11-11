"""What the help button needs in the editors' admin (ARC.32 part 3).

The Wagtail admin has no header and no hook for its account menu (the account menu is drawn by
Wagtail's own sidebar script), so the button is an item of the sidebar's Help menu, next to
"Shortcuts". Its attributes carry the screen's route name, page type and language and the
translated texts of the dialog, so the static script (`cms/js/admin-help.js`) holds no text.

A screen is keyed by its URL name. While a page is edited or added, the page type
(`app_label.ModelName`) keys it too, so one entry can help with every page of a type.
"""

from django.apps import apps
from django.conf import settings
from django.http import HttpRequest
from django.urls import NoReverseMatch, ResolverMatch, reverse
from django.utils import translation
from django.utils.translation import gettext
from wagtail.models import Page

from . import engine


def page_type_label(match: ResolverMatch | None) -> str:
    """The page type of the page a Wagtail admin screen is about, "" for any other screen."""
    if match is None:
        return ""
    kwargs = match.kwargs
    if "page_id" in kwargs:
        page = Page.objects.filter(pk=kwargs["page_id"]).first()
        specific = page.specific_class if page is not None else None
        return specific._meta.label if specific is not None else ""
    app_label = kwargs.get("content_type_app_name")
    model_name = kwargs.get("content_type_model_name")
    if app_label and model_name:
        try:
            model = apps.get_model(app_label, model_name)
        except (LookupError, ValueError):
            return ""
        return model._meta.label if issubclass(model, Page) else ""
    return ""


def menu_attrs(request: HttpRequest) -> dict[str, str]:
    """The data attributes of the opener for this request; empty without the help endpoint."""
    try:
        url = reverse("help_entry")
    except NoReverseMatch:  # a URLconf without the endpoint (a test harness)
        return {}
    match = request.resolver_match
    return {
        "role": "button",  # announced as a button, not a link
        "data-admin-help-open": "",
        "data-help-url": url,
        "data-help-route": engine.help_route_name(getattr(match, "view_name", "")),
        "data-help-page-type": engine.help_page_type(page_type_label(match)),
        "data-help-lang": translation.get_language() or settings.LANGUAGE_CODE,
        "data-help-title": gettext("Help for this page"),
        "data-help-loading": gettext("Loading help…"),
        "data-help-error": gettext("The help text could not be loaded. Try again."),
        "data-help-fields-title": gettext("Fields on this page"),
        "data-help-close": gettext("Close"),
    }
