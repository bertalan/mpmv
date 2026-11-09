"""Wagtail admin hooks of the core app."""

from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from wagtail import hooks

from .models import MediaImage, svg_collection_refusal


@hooks.register("before_bulk_action")  # type: ignore[untyped-decorator]
def keep_svg_in_svg_collections(
    request: HttpRequest, action_type: str, objects: list[Any], action: Any
) -> HttpResponse | None:
    """THM.12: the bulk "Add to collection" action updates the collection with a queryset
    `update()`, which skips `MediaImage.save`. It is refused, with a message, when it would move
    an SVG into a collection that does not accept SVG."""
    if action_type != "add_to_collection" or action.model is not MediaImage:
        return None
    refusal = svg_collection_refusal(action.cleaned_form.cleaned_data["collection"])
    if refusal is None:
        return None
    svgs = [image for image in objects if image.is_svg()]
    if not svgs:
        return None
    titles = ", ".join(image.title for image in svgs)
    messages.error(request, f"Not moved: {titles}. {refusal}")
    response: HttpResponse = redirect(action.next_url)
    return response
