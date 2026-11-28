"""The inline form of an event's vehicle tags (FIT.11): the chooser offers pickable nodes only.

A PROPOSED node is another vendor's pending proposal and a RETIRED one never matches a garage,
so an organiser can pick neither. A row saved earlier keeps its own node on screen when it has
since been retired, so editing the event does not silently drop or reject it.
"""

from typing import Any

from wagtail.admin.forms import WagtailAdminModelForm

from apps.fitment.models import FitmentNode


class EventFitmentForm(WagtailAdminModelForm):  # type: ignore[misc]  # Wagtail is untyped
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        choices = FitmentNode.objects.pickable()
        saved = getattr(self.instance, "node_id", None)
        if saved is not None:
            choices = choices | FitmentNode.objects.filter(pk=saved)
        self.fields["node"].queryset = choices
