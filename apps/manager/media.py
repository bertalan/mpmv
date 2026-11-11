"""The Manager image chooser (VEN.31 part 1, D-34): Wagtail's own chooser modal for Draftail,
served in the shop space for a vendor owner who has no Wagtail admin access.

Wagtail's chooser views are reused for the modal protocol, but every listing, choice and upload
is limited to the selected vendor's own collection (`services.vendor_media_images`), never read
from the request: a request that names another collection, or an image outside the vendor's
collection, is audited (`vendor.media_denied`) and answers 404. Uploads go through
`services.upload_storefront_image` (SEC.2 checks, CMS.8 licence, audit), not Wagtail's form.
"""

from dataclasses import replace
from typing import Any

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from wagtail.admin.modal_workflow import render_modal_workflow
from wagtail.admin.views.generic.chooser import PreserveURLParametersMixin
from wagtail.images import get_image_model
from wagtail.images.forms import ImageInsertionForm
from wagtail.images.views.chooser import (
    ImageChooseResultsView,
    ImageChooseView,
    ImageChosenResponseMixin,
    ImageChosenView,
    ImageSelectFormatView,
)

from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.services import (
    audit_media_denied,
    upload_storefront_image,
    vendor_media_collection,
    vendor_media_images,
)

from .middleware import manager_context
from .navigation import registry

SECTION = "shop.media"
PREFIX = "image-chooser-upload"
#: Request parameters that name a collection (Wagtail's filter and upload forms).
COLLECTION_PARAMS = ("collection_id", "collection", f"{PREFIX}-collection")
PRESERVED = ["multiple", "select_format", "layout"]


class ImageUploadForm(forms.Form):
    """What the vendor sends: the file and a title. Never a collection: that is the vendor's."""

    title = forms.CharField(label=_("Title"), max_length=255, required=False)
    file = forms.FileField(
        label=_("File"),
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}
        ),
    )


class _OwnImages:
    """The gate and the scope shared by every view of the chooser."""

    section = SECTION
    preserve_url_parameters = PRESERVED
    model = get_image_model()
    request: HttpRequest

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        ctx = manager_context(request)
        section = registry.get(SECTION)
        if (
            ctx is None
            or section is None
            or not section.allows(ctx)
            or ctx.vendor is None
            or not isinstance(ctx.user, User)
        ):
            raise Http404("No Manager page here.")
        self.vendor: Vendor = ctx.vendor
        self.actor: User = ctx.user
        self.audit_ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
        own = vendor_media_collection(self.vendor)
        for name in COLLECTION_PARAMS:
            for source in (request.GET, request.POST):
                value = source.get(name, "")
                if value and (own is None or value != str(own.pk)):
                    self.deny(f"collection:{name}", value)
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc,no-any-return]

    def deny(self, what: str, target: str = "") -> None:
        audit_media_denied(self.vendor, ctx=self.audit_ctx, what=what, target=target)
        raise Http404("Not in your library.")

    def own_image(self, pk: Any) -> Any:
        try:
            found = vendor_media_images(self.vendor).filter(pk=int(pk)).first()
        except (TypeError, ValueError):
            found = None
        if found is None:
            self.deny("image", str(pk))
        return found

    def get_object(self, pk: Any) -> Any:
        return self.own_image(pk)


class _OwnListing(_OwnImages):
    chosen_url_name = "manager:shop:image_chosen"
    results_url_name = "manager:shop:image_results"
    create_url_name = "manager:shop:image_create"
    creation_tab_id = "upload"
    create_action_label = _("Upload")
    create_action_clicked_label = _("Uploading…")

    def get_object_list(self) -> Any:
        return vendor_media_images(self.vendor).select_related("collection")

    @property
    def collections(self) -> None:
        return None  # one collection: no choice to offer

    @property
    def layout(self) -> str:
        return "grid"  # the list layout adds a collection column: one collection, nothing to show

    def can_create(self) -> bool:
        return True  # the section check allows owners only

    def get_creation_form_class(self) -> type[ImageUploadForm]:
        return ImageUploadForm

    def get_creation_form_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"prefix": PREFIX}
        if self.request.method in ("POST", "PUT"):
            kwargs.update(data=self.request.POST, files=self.request.FILES)
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context_data(**kwargs)  # type: ignore[misc]
        name = (
            "manager:shop:image_select_format"
            if self.request.GET.get("select_format")
            else "manager:shop:image_chosen"
        )
        for image in context["results"]:
            image.chosen_url = self.append_preserved_url_parameters(  # type: ignore[attr-defined]
                reverse(name, args=(image.id,))
            )
        context["popular_tags"] = []
        return context


class ManagerImageChooseView(_OwnListing, ImageChooseView):  # type: ignore[misc]
    pass


class ManagerImageResultsView(_OwnListing, ImageChooseResultsView):  # type: ignore[misc]
    pass


class ManagerImageChosenView(_OwnImages, ImageChosenView):  # type: ignore[misc]
    pass


class _FormatStep(PreserveURLParametersMixin):  # type: ignore[misc]
    """The "choose a format" step, with the Manager's own action URL."""

    def render_select_format_response(self, image: Any, form: Any) -> HttpResponse:
        response: HttpResponse = render_modal_workflow(
            self.request,
            "wagtailimages/chooser/select_format.html",
            None,
            {
                "image": image,
                "form": form,
                "select_format_action_url": self.append_preserved_url_parameters(
                    reverse("manager:shop:image_select_format", args=(image.id,))
                ),
            },
            json_data={"step": "select_format"},
        )
        return response


class ManagerImageSelectFormatView(_OwnImages, _FormatStep, ImageSelectFormatView):  # type: ignore[misc]
    pass


class ManagerImageUploadView(_OwnListing, _FormatStep, ImageChosenResponseMixin, View):  # type: ignore[misc]
    """The upload tab: validates by the service, then continues like a chosen image."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.form = ImageUploadForm(prefix=PREFIX)
        return self.reshow()

    def reshow(self) -> HttpResponse:
        context = {
            "view": self,
            "creation_form": self.form,
            "create_action_url": self.append_preserved_url_parameters(
                reverse(self.create_url_name)
            ),
            "create_action_label": self.create_action_label,
            "create_action_clicked_label": self.create_action_clicked_label,
        }
        html = render_to_string(
            "wagtailadmin/generic/chooser/creation_form.html", context, self.request
        )
        response: HttpResponse = render_modal_workflow(
            self.request,
            None,
            None,
            None,
            json_data={"step": "reshow_creation_form", "htmlFragment": html},
        )
        return response

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.form = ImageUploadForm(request.POST, request.FILES, prefix=PREFIX)
        if not self.form.is_valid():
            return self.reshow()
        try:
            image = upload_storefront_image(
                self.vendor,
                upload=self.form.cleaned_data["file"],
                title=self.form.cleaned_data["title"] or self.form.cleaned_data["file"].name,
                actor=self.actor,
                ctx=self.audit_ctx,
            )
        except ValidationError as error:
            for message in error.messages:
                self.form.add_error("file", message)
            return self.reshow()
        except PermissionDenied:
            raise Http404("No Manager page here.") from None
        if request.GET.get("select_format"):
            insertion = ImageInsertionForm(
                initial={"alt_text": image.default_alt_text}, prefix="image-chooser-insertion"
            )
            return self.render_select_format_response(image, insertion)
        return self.get_chosen_response(image)  # type: ignore[no-any-return]
