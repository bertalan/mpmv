"""FIT.11: the vehicle chooser of an event offers pickable (ACTIVE) nodes only.

A PROPOSED node (another vendor's pending proposal) and a RETIRED node are neither offered nor
accepted by form validation. A row saved earlier keeps its node visible after the node is retired.
"""

from typing import Any

import pytest

from apps.events.models import EventPage
from apps.events.tests.factories import EventFitmentFactory, EventPageFactory
from apps.events.tests.integration.test_map_picker import _data, _form, _staff
from apps.fitment.models import FitmentNode
from apps.fitment.tests.factories import FitmentNodeFactory

pytestmark = pytest.mark.django_db

Status = FitmentNode.Status


def _tagged(page: EventPage, node: FitmentNode, *, initial: int = 0) -> Any:
    data = _data(page)
    data["fitment_rows-TOTAL_FORMS"] = str(initial + 1)
    data["fitment_rows-INITIAL_FORMS"] = str(initial)
    data[f"fitment_rows-{initial}-node"] = str(node.pk)
    data[f"fitment_rows-{initial}-id"] = ""
    data[f"fitment_rows-{initial}-ORDER"] = "1"
    data[f"fitment_rows-{initial}-DELETE"] = ""
    return _form(page, _staff(), data)


def test_the_chooser_offers_active_nodes_only() -> None:
    active = FitmentNodeFactory.create(name="Acme", status=Status.ACTIVE)
    proposed = FitmentNodeFactory.create(name="Pending", status=Status.PROPOSED)
    retired = FitmentNodeFactory.create(name="Gone", status=Status.RETIRED)
    page = EventPageFactory.create()
    formset = _form(page, _staff(), _data(page)).formsets["fitment_rows"]
    offered = set(formset.empty_form.fields["node"].queryset.values_list("pk", flat=True))
    assert active.pk in offered
    assert proposed.pk not in offered
    assert retired.pk not in offered


@pytest.mark.parametrize("status", [Status.PROPOSED, Status.RETIRED])
def test_validation_refuses_a_node_that_is_not_pickable(status: str) -> None:
    form = _tagged(EventPageFactory.create(), FitmentNodeFactory.create(status=status))
    assert not form.is_valid()
    assert form.formsets["fitment_rows"].errors[0].get("node")


def test_validation_accepts_an_active_node() -> None:
    form = _tagged(EventPageFactory.create(), FitmentNodeFactory.create(status=Status.ACTIVE))
    assert form.is_valid(), form.errors


def test_a_saved_row_keeps_its_node_after_the_node_is_retired() -> None:
    node = FitmentNodeFactory.create(status=Status.ACTIVE)
    page = EventPageFactory.create()
    row = EventFitmentFactory.create(page=page, node=node)
    node.status = Status.RETIRED
    node.save()
    data = _data(page)
    data.update(
        {
            "fitment_rows-TOTAL_FORMS": "1",
            "fitment_rows-INITIAL_FORMS": "1",
            "fitment_rows-0-id": str(row.pk),
            "fitment_rows-0-node": str(node.pk),
            "fitment_rows-0-ORDER": "1",
            "fitment_rows-0-DELETE": "",
        }
    )
    form = _form(page, _staff(), data)
    offered = set(
        form.formsets["fitment_rows"].forms[0].fields["node"].queryset.values_list("pk", flat=True)
    )
    assert node.pk in offered
    assert form.is_valid(), form.errors
