"""Wagtail admin views of the core app (THM.12)."""

from typing import Any

from django.core.exceptions import ValidationError
from wagtail.admin.forms.collections import CollectionForm
from wagtail.admin.views import collections

from .models import svg_collection_move_refusal


class SvgAwareCollectionForm(CollectionForm):  # type: ignore[misc]  # Wagtail is untyped
    """Wagtail's collection form, refusing a move that would carry SVG images out of the
    collections for logos and icons (moving a collection never saves its images)."""

    def clean_parent(self) -> Any:
        parent = super().clean_parent()
        if not self.instance._state.adding and parent.pk != self.initial.get("parent"):
            refusal = svg_collection_move_refusal(self.instance, parent)
            if refusal is not None:
                raise ValidationError(refusal)
        return parent


class CollectionEditView(collections.Edit):  # type: ignore[misc]
    """Wagtail's collection edit view with `SvgAwareCollectionForm`."""

    form_class = SvgAwareCollectionForm
