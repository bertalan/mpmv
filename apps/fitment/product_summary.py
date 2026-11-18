"""The fitment summary of the public product page (THM.7 part 3, the list of FIT.9).

Registered on the catalog's `catalog_product_fitment` hook (`wagtail_hooks.py`), so the catalog
never imports this app. Rows are grouped by the top-level node (the make) and read in a fixed
number of queries: the rows, their ancestors, the names and the notes, whatever the row count.
The summary is the same for every visitor: a garage never changes it (FIT.9).
"""

from collections import defaultdict

from django.utils.translation import gettext as _

from apps.catalog.models import Product
from apps.catalog.product_pages import FitmentEntry, FitmentGroup, FitmentSummary
from apps.core import translations

from .engine import STEP_LENGTH
from .models import FitmentNode, ProductFitment, ProductFitmentState


def _name(node: FitmentNode, language: str) -> str:
    """The node's name: translated only when its level translates names."""
    if node.level.translate_names:
        found = translations.resolve(node.translations.all(), language).translation
        if found is not None:
            return str(found.name)
    return str(node.name)


def _years(row: ProductFitment) -> str:
    low, high = row.year_from, row.year_to
    if low is None and high is None:
        return ""
    if low is not None and high is not None:
        return str(low) if low == high else f"{low}\u2013{high}"
    if low is not None:
        return _("from %(year)s") % {"year": low}
    return _("until %(year)s") % {"year": high}


def summary(product: Product, language: str) -> FitmentSummary | None:
    state = ProductFitmentState.objects.filter(product=product).first()
    if state is None or state.mode == ProductFitmentState.Mode.UNDECLARED:
        return None
    if state.mode == ProductFitmentState.Mode.UNIVERSAL:
        return FitmentSummary(universal=True)
    rows = list(
        ProductFitment.objects.filter(product=product)
        .select_related("node__level", "note")
        .prefetch_related("node__translations", "note__translations")
        .order_by("node_path", "id")
    )
    if not rows:
        return None
    roots = {row.node_path[:STEP_LENGTH] for row in rows}
    makes = {
        node.path: node
        for node in FitmentNode.objects.filter(path__in=roots)
        .select_related("level")
        .prefetch_related("translations")
    }
    grouped: dict[str, list[FitmentEntry]] = defaultdict(list)
    for row in rows:
        root = row.node_path[:STEP_LENGTH]
        make = makes.get(root)
        if make is None:
            continue
        label = _name(row.node, language) if row.node_path != root else _("All models")
        note = ""
        if row.note is not None:
            found = translations.resolve(row.note.translations.all(), language).translation
            note = str(found.text) if found is not None else ""
        grouped[root].append(FitmentEntry(label, _years(row), note, row.is_exclusion))
    return FitmentSummary(
        groups=tuple(
            FitmentGroup(_name(makes[root], language), tuple(entries))
            for root, entries in sorted(grouped.items())
        )
    )
