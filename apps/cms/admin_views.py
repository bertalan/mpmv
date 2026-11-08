"""Editors' account page in the Wagtail admin (CMS.1), without the panels this project lacks.

Wagtail's account page always shows a first and last name form, but our User has `full_name`:
the form's fields are not model fields, so it would demand a name it then discards and block
every other preference from being saved. It also offers a profile picture upload, which no
spec item plans for. Both panels are left out; language, time zone, theme and notification
preferences stay. `admin_urls` mounts this view at the admin's own `account/` path.

`localize_image_info` (I18N.3) answers the image preview of wagtail-localize's translation
editor, which still asks Wagtail's former admin API (`api/main/images/<id>/`, gone in Wagtail 8).

`image_review` (CMS.8) lists the images still missing their licence or author, typically
migrated without known metadata, with a link to classify each in Wagtail's image editor.
"""

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils.translation import gettext as _
from wagtail.admin.views.account import (
    AccountView,
    AvatarSettingsPanel,
    BaseSettingsPanel,
    NameEmailSettingsPanel,
)
from wagtail.images import get_image_model
from wagtail.images.permissions import permission_policy as image_permissions

from apps.core.services import images_needing_review

OMITTED_PANELS: tuple[type[BaseSettingsPanel], ...] = (NameEmailSettingsPanel, AvatarSettingsPanel)


class EditorAccountView(AccountView):  # type: ignore[misc]  # Wagtail is untyped
    def get_panels(self) -> list[Any]:
        panels: list[Any] = super().get_panels()
        return [panel for panel in panels if not isinstance(panel, OMITTED_PANELS)]


PREVIEW_FILTER = "max-165x165"  # the size of Wagtail's own chooser previews


def localize_image_info(request: HttpRequest, pk: int) -> JsonResponse:
    """`{id, title, thumbnail: {url, width, height}}`, the shape the translation editor reads."""
    image = get_object_or_404(get_image_model(), pk=pk)
    if not image_permissions.user_has_any_permission_for_instance(
        request.user, ["choose", "change"], image
    ):
        raise PermissionDenied
    thumbnail = image.get_rendition(PREVIEW_FILTER)
    return JsonResponse(
        {
            "id": image.pk,
            "title": image.title,
            "thumbnail": {
                "url": thumbnail.url,
                "width": thumbnail.width,
                "height": thumbnail.height,
            },
        }
    )


REVIEW_PAGE_SIZE = 200


def can_classify_images(request: HttpRequest) -> bool:
    return bool(image_permissions.user_has_permission(request.user, "change"))


def image_review(request: HttpRequest) -> TemplateResponse:
    """Images to classify: no licence or no author. Classified images leave the list."""
    if not can_classify_images(request):
        raise PermissionDenied
    images = images_needing_review().select_related("licence")
    return TemplateResponse(
        request,
        "cms/admin/image_review.html",
        {
            "page_title": _("Images to classify"),
            "header_icon": "image",
            "images": images[:REVIEW_PAGE_SIZE],
            "total": images.count(),
        },
    )
