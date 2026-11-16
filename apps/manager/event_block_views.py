"""Manager shop space: the body blocks, the image library and the preview of the vendor's own
events (EVT.4 part 2, spec 15 "Vendor area").

Thin adapters over the EVT.3 services. Each block action loads the event through `scoped()` (another
shop's id is a 404 before anything runs), changes the body of its latest revision with one
`save_event_draft` call (vendor-safe checks and the sanitiser run each time) and answers with the
block panel (htmx) or a redirect to the form (plain post). Every POST is the group 1 session action
`content_edit` (CORE.25).
"""

from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache

from apps.accounts.decorators import requires_level_method
from apps.core.models import ImageLicence
from apps.events import engine, services
from apps.events.models import EventPage
from apps.vendors.engine import VendorCannotWrite

from .event_block_forms import FORMS, BlockForm
from .event_block_panel import PANEL_ID, blocks_context
from .event_forms import EventImageForm
from .event_views import EDIT_URL, _EventAction, _not_active


class _BlockView(_EventAction):
    """An action on the body of one of the shop's events."""

    def respond(self, request: HttpRequest, page: EventPage) -> HttpResponse:
        if request.headers.get("HX-Request"):
            html = render_to_string(
                "manager/shop/_event_blocks.html", blocks_context(page), request=request
            )
            return HttpResponse(html)
        return HttpResponseRedirect(reverse(EDIT_URL, args=[page.public_id]) + f"#{PANEL_ID}")

    def refused(self, request: HttpRequest, error: Exception) -> str:
        """The error of a refused service call, as text for the form or a message."""
        if isinstance(error, ValidationError):
            return " ".join(error.messages)
        if isinstance(error, VendorCannotWrite):
            return str(_not_active())
        return _("You cannot change this event.")


class _BlockFormView(_BlockView):
    template_name = "manager/shop/event_block_form.html"

    def kind(self) -> str:
        raise NotImplementedError

    def form_class(self) -> type[BlockForm]:
        found = FORMS.get(self.kind())
        if found is None:
            raise Http404("No such block.")
        return found

    def render_form(self, form: BlockForm, page: EventPage, **extra: Any) -> HttpResponse:
        context = self.get_context_data(
            form=form,
            event=page,
            block_label=form.block_label,
            kind=form.kind,
            edit_url=reverse(EDIT_URL, args=[page.public_id]),
            title=_("Edit the block") if "block_id" in self.kwargs else _("Add a block"),
            **extra,
        )
        return self.render_to_response(context)

    def save(self, page: EventPage, form: BlockForm) -> None:
        raise NotImplementedError

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page, vendor = self.event(), self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        form = self.form_class()(request.POST, vendor=vendor)
        if not form.is_valid():
            return self.render_form(form, page)
        try:
            self.save(page, form)
        except engine.BlockNotFound:
            raise Http404("No such block.") from None
        except ValidationError as error:
            form.add_error(None, self.refused(request, error))
            return self.render_form(form, page)
        except (services.EventRefused, VendorCannotWrite, PermissionDenied) as error:
            form.add_error(None, self.refused(request, error))
            return self.render_form(form, page)
        return self.respond(request, page)


class EventBlockAddView(_BlockFormView):
    """GET `?type=` shows the form of one block type, POST adds it to the end of the body."""

    def kind(self) -> str:
        return str(self.request.GET.get("type") or self.request.POST.get("type") or "")

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page, vendor = self.event(), self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        return self.render_form(self.form_class()(vendor=vendor), page)

    def save(self, page: EventPage, form: BlockForm) -> None:
        services.add_body_block(
            self.actor(),
            page,
            form.kind,
            form.raw_value(),
            vendor=self.manager.vendor,
            ctx=self.audit(self.request),
        )

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().post(request, *args, **kwargs)


class EventBlockEditView(_BlockFormView):
    """GET shows the block's form filled with its value, POST replaces it."""

    def block(self, page: EventPage) -> dict[str, Any]:
        try:
            return dict(engine.block_by_id(services.event_body(page), self.kwargs["block_id"]))
        except engine.BlockNotFound:
            raise Http404("No such block.") from None

    def kind(self) -> str:
        return str(self.block(self.event())["type"])

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page, vendor = self.event(), self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        block = self.block(page)
        form_class = self.form_class()
        form = form_class(initial=form_class.initial_for(block["value"]), vendor=vendor)
        return self.render_form(form, page)

    def save(self, page: EventPage, form: BlockForm) -> None:
        services.replace_body_block(
            self.actor(),
            page,
            self.kwargs["block_id"],
            form.raw_value(),
            vendor=self.manager.vendor,
            ctx=self.audit(self.request),
        )

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().post(request, *args, **kwargs)


class EventBlockMoveView(_BlockView):
    http_method_names = ["post", "options"]

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page = self.event()
        direction = request.POST.get("direction", "")
        if direction not in ("up", "down"):
            return HttpResponseBadRequest("direction must be up or down")
        try:
            services.move_body_block(
                self.actor(),
                page,
                self.kwargs["block_id"],
                direction,
                vendor=self.manager.vendor,
                ctx=self.audit(request),
            )
        except engine.BlockNotFound:
            raise Http404("No such block.") from None
        except (
            ValidationError,
            services.EventRefused,
            VendorCannotWrite,
            PermissionDenied,
        ) as error:
            messages.error(request, self.refused(request, error))
        return self.respond(request, page)


class EventBlockRemoveView(_BlockView):
    http_method_names = ["post", "options"]

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page = self.event()
        try:
            services.remove_body_block(
                self.actor(),
                page,
                self.kwargs["block_id"],
                vendor=self.manager.vendor,
                ctx=self.audit(request),
            )
        except engine.BlockNotFound:
            raise Http404("No such block.") from None
        except (
            ValidationError,
            services.EventRefused,
            VendorCannotWrite,
            PermissionDenied,
        ) as error:
            messages.error(request, self.refused(request, error))
        return self.respond(request, page)


@method_decorator(never_cache, name="dispatch")
class EventPreviewView(_EventAction):
    """GET: the latest revision as visitors will see it, read only and not for search engines."""

    http_method_names = ["get", "head", "options"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page = self.event()
        latest: EventPage = page.get_latest_revision_as_object()
        mode = latest.default_preview_mode
        response = latest.make_preview_request(request, mode)  # serves the revision as a response
        if hasattr(response, "render"):
            response.render()
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response  # type: ignore[no-any-return]


class EventImagesView(_EventAction):
    """The shop's image library, with the upload form (not tied to one event)."""

    template_name = "manager/shop/event_images.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.manager.vendor
        context.setdefault("form", EventImageForm())
        context["images"] = list(services.vendor_image_library(vendor)[:100]) if vendor else []
        context["title"] = _("Images")
        context["list_url"] = reverse("manager:shop:events")
        return context

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor = self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        form = EventImageForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        data = form.cleaned_data
        licence: ImageLicence = data["licence"]
        try:
            services.upload_event_image(
                self.actor(),
                vendor,
                upload=data["file"],
                title=data["title"],
                licence=licence,
                author=data["author"],
                source_url=data["source_url"],
                credit_text=data["credit_text"],
                permission_ref=data["permission_ref"],
                ctx=self.audit(request),
            )
        except ValidationError as error:
            _add_errors(form, error)
            return self.render_to_response(self.get_context_data(form=form))
        except VendorCannotWrite:
            form.add_error(None, _not_active())
            return self.render_to_response(self.get_context_data(form=form))
        messages.success(request, _("The image is saved in your library."))
        return HttpResponseRedirect(reverse("manager:shop:event_images"))


def _add_errors(form: EventImageForm, error: ValidationError) -> None:
    if hasattr(error, "error_dict"):
        for name, errors in error.message_dict.items():
            for message in errors:
                form.add_error(name if name in form.fields else None, message)
    else:
        for message in error.messages:
            form.add_error(None, message)
