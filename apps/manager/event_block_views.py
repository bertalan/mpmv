"""Manager shop space: the body blocks, the image library and the preview of the vendor's own
events (EVT.4 part 2, spec 15 "Vendor area").

Thin adapters over the EVT.3 services. Each block action loads the event through `scoped()` (another
shop's id is a 404 before anything runs), changes the body of its latest revision with one
`save_event_draft` call (vendor-safe checks and the sanitiser run each time) and answers with the
block panel (htmx) or a redirect to the form (plain post). Every POST is the group 1 session action
`content_edit` (CORE.25).
"""

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
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
from apps.core.models import ImageLicence, MediaImage
from apps.events import engine, services
from apps.events.models import EventPage
from apps.vendors.engine import VendorCannotWrite

from .event_block_forms import FORMS, BlockForm
from .event_block_panel import PANEL_ID, block_types, blocks_context
from .event_image_form import EventImageForm
from .event_views import EDIT_URL, _EventAction, _not_active


def _base_revision(request: HttpRequest) -> int | None:
    """The revision the editor was rendered from (None: not sent; -1: not a number, so stale)."""
    raw = request.POST.get("base_revision")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return -1


IMAGES_PER_PAGE = 24


class _BlockView(_EventAction):
    """An action on the body of one of the shop's events."""

    def respond(self, request: HttpRequest, page: EventPage) -> HttpResponse:
        if request.headers.get("HX-Request"):
            html = render_to_string(
                "manager/shop/_event_blocks.html", blocks_context(page), request=request
            )
            return HttpResponse(html)
        return HttpResponseRedirect(reverse(EDIT_URL, args=[page.public_id]) + f"#{PANEL_ID}")

    def stale(self, request: HttpRequest, error: Exception) -> HttpResponse:
        """A move or remove made on an older body: nothing is saved. htmx gets a 409 and reloads
        the page, which shows the message; a plain post is redirected with the message."""
        messages.error(request, self.refused(request, error))
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=409)
            response["HX-Refresh"] = "true"
            return response
        return self.respond(request, self.event())

    def refused(self, request: HttpRequest, error: Exception) -> str:
        """The error of a refused service call, as text for the form or a message."""
        if isinstance(error, services.StaleBlockEdit):
            return _(
                "This event was changed by someone else since you opened it. "
                "Reload the page and make your change again."
            )
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
        extra.setdefault("base_revision", services.event_revision_id(page))
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

    def save(self, page: EventPage, form: BlockForm, base_revision: int | None) -> None:
        raise NotImplementedError

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page, vendor = self.event(), self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        form = self.form_class()(request.POST, vendor=vendor)
        posted = request.POST.get("base_revision")
        shown = {"base_revision": posted} if posted is not None else {}
        if not form.is_valid():
            return self.render_form(form, page, **shown)
        try:
            self.save(page, form, _base_revision(request))
        except engine.BlockNotFound:
            raise Http404("No such block.") from None
        except ValidationError as error:
            form.add_error(None, self.refused(request, error))
            return self.render_form(form, page, **shown)
        except (services.EventRefused, VendorCannotWrite, PermissionDenied) as error:
            form.add_error(None, self.refused(request, error))
            return self.render_form(form, page, **shown)
        return self.respond(request, page)


class EventBlockAddView(_BlockFormView):
    """GET `?type=` shows the form of one block type (no type: the list of types), POST adds it
    to the end of the body."""

    @property
    def template_name(self) -> str:  # type: ignore[override]
        if self.request.method == "GET" and not self.kind():
            return "manager/shop/event_block_types.html"
        return "manager/shop/event_block_form.html"

    def kind(self) -> str:
        return str(self.request.GET.get("type") or self.request.POST.get("type") or "")

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page, vendor = self.event(), self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        if (
            not self.kind()
        ):  # no type chosen: the page that lists the types (an unknown one is a 404)
            return self.render_to_response(
                self.get_context_data(
                    event=page,
                    title=_("Add a block"),
                    edit_url=reverse(EDIT_URL, args=[page.public_id]),
                    types=[
                        (f"{self.request.path}?{urlencode({'type': kind})}", label)
                        for kind, label in block_types()
                    ],
                )
            )
        return self.render_form(self.form_class()(vendor=vendor), page)

    def save(self, page: EventPage, form: BlockForm, base_revision: int | None) -> None:
        services.add_body_block(
            self.actor(),
            page,
            form.kind,
            form.raw_value(),
            vendor=self.manager.vendor,
            ctx=self.audit(self.request),
            base_revision=base_revision,
        )

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not self.kind():
            return HttpResponseRedirect(
                reverse(EDIT_URL, args=[self.event().public_id]) + f"#{PANEL_ID}"
            )
        return super().post(request, *args, **kwargs)


class EventBlockEditView(_BlockFormView):
    """GET shows the block's form filled with its value, POST replaces it."""

    def block(self, page: EventPage) -> dict[str, Any]:
        try:
            return dict(engine.block_by_id(services.event_body(page), str(self.kwargs["block_id"])))
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

    def save(self, page: EventPage, form: BlockForm, base_revision: int | None) -> None:
        services.replace_body_block(
            self.actor(),
            page,
            str(self.kwargs["block_id"]),
            form.raw_value(),
            vendor=self.manager.vendor,
            ctx=self.audit(self.request),
            base_revision=base_revision,
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
                str(self.kwargs["block_id"]),
                direction,
                vendor=self.manager.vendor,
                ctx=self.audit(request),
                base_revision=_base_revision(request),
            )
        except engine.BlockNotFound:
            raise Http404("No such block.") from None
        except services.StaleBlockEdit as error:
            return self.stale(request, error)
        except (
            ValidationError,
            services.EventRefused,
            VendorCannotWrite,
            PermissionDenied,
        ) as error:
            messages.error(request, self.refused(request, error))
        return self.respond(request, page)


class EventBlockRemoveView(_BlockView):
    """GET asks "Remove this block?" (no JavaScript; htmx asks in the browser), POST removes."""

    template_name = "manager/shop/event_block_remove.html"
    http_method_names = ["get", "head", "post", "options"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page = self.event()
        try:
            block = engine.block_by_id(services.event_body(page), str(kwargs["block_id"]))
        except engine.BlockNotFound:
            raise Http404("No such block.") from None
        labels = dict(block_types())
        kind = str(block.get("type"))
        return self.render_to_response(
            self.get_context_data(
                event=page,
                title=_("Remove this block?"),
                block_label=str(labels.get(kind, kind)),
                summary=engine.block_summary(block),
                edit_url=reverse(EDIT_URL, args=[page.public_id]) + f"#{PANEL_ID}",
                base_revision=services.event_revision_id(page),
            )
        )

    @requires_level_method("content_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        page = self.event()
        try:
            services.remove_body_block(
                self.actor(),
                page,
                str(self.kwargs["block_id"]),
                vendor=self.manager.vendor,
                ctx=self.audit(request),
                base_revision=_base_revision(request),
            )
        except engine.BlockNotFound:
            raise Http404("No such block.") from None
        except services.StaleBlockEdit as error:
            return self.stale(request, error)
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
        response = latest.make_preview_request(
            request, mode, extra_request_attrs={"resolver_match": request.resolver_match}
        )  # the route keys the help button
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
        library = services.vendor_image_library(vendor) if vendor else MediaImage.objects.none()
        paginator = Paginator(library, IMAGES_PER_PAGE)
        number = self.request.GET.get("page")
        pager = paginator.get_page(number)
        context["images"] = list(pager)
        context["pager"] = pager
        context["page_count"] = paginator.num_pages
        context["previous_url"] = (
            f"?page={pager.previous_page_number()}" if pager.has_previous() else ""
        )
        context["next_url"] = f"?page={pager.next_page_number()}" if pager.has_next() else ""
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
        except PermissionDenied:
            form.add_error(None, _("You cannot add images for this shop."))
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
