"""CAT.3: staff manage the attribute catalogue (attributes, values, their texts) in the admin."""

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Attribute, AttributeValue
from apps.catalog.tests.factories import clothing_attributes

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def _formset(prefix: str, rows: list[dict[str, str]]) -> dict[str, str]:
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        data.update({f"{prefix}-{i}-{name}": value for name, value in row.items()})
    return data


def test_staff_add_an_attribute_with_names_and_values(staff_client: Client) -> None:
    data = {
        "code": "finish",
        "kind": "choice",
        "unit": "",
        "position": "0",
        **_formset("translations", [{"language": "en", "name": "Finish", "status": "published"}]),
        **_formset(
            "values", [{"value": "matte", "position": "0"}, {"value": "gloss", "position": "1"}]
        ),
    }
    response = staff_client.post(reverse("admin:catalog_attribute_add"), data)
    assert response.status_code == 302, response.content.decode()[:2000]
    attribute = Attribute.objects.get(code="finish")
    assert attribute.translations.get().name == "Finish"
    assert list(attribute.values.values_list("value", flat=True)) == ["matte", "gloss"]


def test_staff_translate_a_value(staff_client: Client) -> None:
    red = clothing_attributes()["color"].values.get(value="red")
    data = {
        "attribute": str(red.attribute_id),
        "value": "red",
        "position": "0",
    }
    # Keep the existing EN and IT rows: post them as initial forms.
    existing = list(red.translations.order_by("language"))
    rows = [
        {
            "id": str(row.pk),
            "value": str(red.pk),
            "language": row.language,
            "label": row.label,
            "status": row.status,
        }
        for row in existing
    ]
    data.update(
        _formset("translations", [*rows, {"language": "de", "label": "Rot", "status": "published"}])
    )
    data["translations-INITIAL_FORMS"] = str(len(existing))
    url = reverse("admin:catalog_attributevalue_change", args=[red.pk])
    response = staff_client.post(url, data)
    assert response.status_code == 302, response.content.decode()[:2000]
    assert AttributeValue.objects.get(pk=red.pk).translation("de").translation.label == "Rot"  # type: ignore[union-attr]


def test_the_attribute_admin_pages_load(staff_client: Client) -> None:
    clothing_attributes()
    for name in ("catalog_attribute_changelist", "catalog_attributevalue_changelist"):
        assert staff_client.get(reverse(f"admin:{name}")).status_code == 200
