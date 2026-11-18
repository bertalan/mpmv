"""Reserved slugs of root-level CMS pages (ARC.37).

The customer help centre lives at `/help-centre/`, ahead of the Wagtail catch-all, so a page
called that directly under a language home would be shadowed. Wagtail has no hook for slug
rules: `Page._slug_is_available` (used by the page form, move and bulk move) is wrapped once from
`CmsConfig.ready`, and `Page.clean` and the admin page form show a clear message.
"""

from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext
from wagtail.admin.forms.pages import WagtailAdminPageForm
from wagtail.models import Page

from . import engine

_WRAPPED = "_cms_reserved_slugs"


def message(slug: str) -> str:
    return gettext(
        "The slug '%(slug)s' is reserved for the site's help pages and cannot be used for a "
        "page directly under the home page. Choose another slug."
    ) % {"slug": slug}


def _parent_depth(parent: Any) -> int | None:
    return getattr(parent, "depth", None)


def install() -> None:
    if getattr(Page, _WRAPPED, False):
        return
    available = Page._slug_is_available
    clean = Page.clean
    form_clean = WagtailAdminPageForm.clean

    def slug_is_available(slug: Any, parent_page: Any, page: Any = None) -> bool:
        if engine.is_reserved_root_slug(slug, _parent_depth(parent_page)):
            return False
        return bool(available(slug, parent_page, page))

    def page_clean(self: Any) -> None:
        if self.pk is not None and engine.is_reserved_root_slug(
            self.slug, _parent_depth(self.get_parent())
        ):
            raise ValidationError({"slug": message(self.slug)})
        clean(self)

    def page_form_clean(self: Any) -> Any:
        slug = self.cleaned_data.get("slug")  # the parent clean drops it on its own error
        data = form_clean(self)
        if engine.is_reserved_root_slug(slug, _parent_depth(self.parent_page)):
            self._errors.pop("slug", None)  # replace the generic "already in use" error
            self.add_error("slug", ValidationError(message(slug)))
        return data

    Page._slug_is_available = staticmethod(slug_is_available)
    Page.clean = page_clean
    WagtailAdminPageForm.clean = page_form_clean
    setattr(Page, _WRAPPED, True)
