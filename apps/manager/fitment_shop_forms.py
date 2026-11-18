"""Forms of the shop's Fits pages (FIT.3). Validation of shape only: the rules (year bounds,
contradictions, the row limit, the proposal limit) stay in `apps.fitment.services`."""

import uuid
from typing import Any

from django import forms
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core import translations
from apps.fitment.engine import STEP_LENGTH
from apps.fitment.models import DeclarationMode, FitmentNode, FitmentNote, NodeStatus
from apps.fitment.services import RowInput

type Choices = list[tuple[str, str]]

# The declaration modes as the shop reads them (the model's labels are not in the catalogs).
MODE_CHOICES = (
    (DeclarationMode.SPECIFIC.value, _("Specific items")),
    (DeclarationMode.UNIVERSAL.value, _("Fits everything")),
    (DeclarationMode.UNDECLARED.value, _("Not declared")),
)
MODE_LABELS = dict(MODE_CHOICES)


def _choose(field: forms.Field, choices: Choices) -> None:
    assert isinstance(field, forms.ChoiceField)  # noqa: S101  # narrows the type for mypy
    field.choices = choices


SEPARATOR = " \u203a "


def node_choices(*, leaves_only: bool = False, skip_last_level: bool = False) -> Choices:
    """The approved nodes as `Make > Model > Variant`, in tree order: one query, and the native
    select that works without a script. A node under a proposed or retired parent is left out."""
    nodes = list(FitmentNode.objects.filter(status=NodeStatus.ACTIVE).order_by("path"))
    names = {n.path: n.name for n in nodes}
    max_depth = max((n.get_depth() for n in nodes), default=0)
    choices: Choices = []
    for node in nodes:
        parts = [
            names.get(node.path[:end])
            for end in range(STEP_LENGTH, len(node.path) + 1, STEP_LENGTH)
        ]
        if None in parts:
            continue  # an ancestor is not approved
        if skip_last_level and node.get_depth() >= max_depth and max_depth > 1:
            continue
        choices.append((str(node.public_id), SEPARATOR.join(str(p) for p in parts)))
    return choices


def note_choices() -> Choices:
    language = get_language() or translations.source_language()
    out: Choices = []
    for note in FitmentNote.objects.prefetch_related("translations").order_by("code"):
        found = translations.resolve(note.translations.all(), language).translation
        out.append((str(note.pk), str(found.text) if found is not None else note.code))
    return out


class RowForm(forms.Form):
    """One row of the fitment table. A blank row is ignored."""

    node = forms.ChoiceField(required=False, label=_("Item"))
    year_from = forms.IntegerField(required=False, label=_("From year"), min_value=1000)
    year_to = forms.IntegerField(required=False, label=_("To year"), min_value=1000)
    is_exclusion = forms.BooleanField(required=False, label=_("Excludes"))
    note = forms.ChoiceField(required=False, label=_("Note"))

    def __init__(self, *args: Any, nodes: Choices, notes: Choices, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _choose(self.fields["node"], [("", "—"), *nodes])
        _choose(self.fields["note"], [("", "—"), *notes])
        for _name, field in self.fields.items():
            field.widget.attrs["aria-label"] = str(field.label)
        self.empty_permitted = True

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if not data.get("node") and any(
            data.get(k) for k in ("year_from", "year_to", "is_exclusion", "note")
        ):
            self.add_error("node", _("Choose the item this row is about."))
        return data


class ModeForm(forms.Form):
    mode = forms.ChoiceField(label=_("Declaration"), choices=MODE_CHOICES, widget=forms.RadioSelect)


class CopyForm(forms.Form):
    source = forms.ChoiceField(label=_("Copy from another product"))

    def __init__(self, *args: Any, sources: Choices, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _choose(self.fields["source"], sources)


class ProposalForm(forms.Form):
    name = forms.CharField(label=_("Name of the missing item"), max_length=120)
    parent = forms.ChoiceField(
        label=_("Belongs under"),
        required=False,
        help_text=_("Leave empty for the top level."),
    )

    def __init__(self, *args: Any, parents: Choices, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _choose(self.fields["parent"], [("", str(_("Top level"))), *parents])

    def parent_node(self) -> FitmentNode | None:
        value = self.cleaned_data.get("parent")
        if not value:
            return None
        found: FitmentNode | None = FitmentNode.objects.filter(public_id=uuid.UUID(value)).first()
        return found


def row_inputs(forms_: list[RowForm]) -> list[RowInput]:
    """The `RowInput`s of the valid, non-blank forms (the deleted ones are skipped)."""
    filled = [
        f.cleaned_data
        for f in forms_
        if f.cleaned_data.get("node") and not f.cleaned_data.get("DELETE")
    ]
    nodes = {
        str(n.public_id): n
        for n in FitmentNode.objects.filter(public_id__in=[uuid.UUID(d["node"]) for d in filled])
    }
    notes = {
        str(n.pk): n
        for n in FitmentNote.objects.filter(pk__in=[d["note"] for d in filled if d["note"]])
    }
    return [
        RowInput(
            nodes[d["node"]],
            d.get("year_from"),
            d.get("year_to"),
            bool(d.get("is_exclusion")),
            notes.get(d.get("note") or ""),
        )
        for d in filled
        if d["node"] in nodes
    ]


def other_products(product: Product, queryset: Any) -> Choices:
    return [
        (str(p.public_id), p.sku) for p in queryset.exclude(pk=product.pk).order_by("sku")[:500]
    ]
