"""FIT.17: an order placed with an active garage vehicle keeps a snapshot of it (Q-G8).

Done-when covered here: the snapshot is stored with the order (never a live reference), it shows
in the customer and the vendor emails in five languages, an order without a vehicle has no line,
a malformed vehicle is refused, and the scrub clears it. The order summary line is covered by
the e2e test (tests/e2e/test_order_vehicle_line.py) and the component unit test.
"""

import pytest

from apps.core import services as core_services
from apps.core.models import OutboxEmail
from apps.orders import notifications
from apps.orders.models import Order
from apps.orders.tests.integration import test_retention
from apps.orders.tests.integration.test_services import pay, place

pytestmark = pytest.mark.django_db

LANGS = ("en", "it", "de", "fr", "es")
VEHICLE = {"make": "Acme", "model": "Roadster", "year": "2019"}
PHRASES = {
    "en": "Vehicle selected by the customer in the compatibility filter: Acme, Roadster, 2019"
    " — compatibility declared by the vendor",
    "it": "Veicolo selezionato dal cliente nel filtro di compatibilità: Acme, Roadster, 2019"
    " — compatibilità dichiarata dal venditore",
    "de": "Vom Kunden im Kompatibilitätsfilter gewähltes Fahrzeug: Acme, Roadster, 2019"
    " — Kompatibilität vom Verkäufer angegeben",
    "fr": "Véhicule sélectionné par le client dans le filtre de compatibilité :"
    " Acme, Roadster, 2019 — compatibilité déclarée par le vendeur",
    "es": "Vehículo seleccionado por el cliente en el filtro de compatibilidad: Acme, Roadster,"
    " 2019 — compatibilidad declarada por el vendedor",
}


def test_the_order_keeps_the_vehicle_labels_and_nothing_else() -> None:
    order = place(vehicle={**VEHICLE, "plate": "XX000XX"})
    order.refresh_from_db()
    assert order.vehicle_snapshot == VEHICLE


def test_an_order_without_a_vehicle_has_an_empty_snapshot() -> None:
    assert place().vehicle_snapshot == {}


def test_a_malformed_vehicle_is_refused_and_no_order_is_created() -> None:
    with pytest.raises(ValueError, match="vehicle"):
        place(vehicle={"make": "Acme", "model": 5, "year": "2019"})
    assert Order.objects.count() == 0


def test_the_audit_snapshot_says_a_vehicle_was_kept_without_its_labels() -> None:
    from apps.core.models import AuditLog

    place(vehicle=VEHICLE)
    entry = AuditLog.objects.filter(action="orders.create").order_by("id").first()
    assert entry is not None
    assert "Roadster" not in str(entry.after)


@pytest.mark.parametrize("lang", LANGS)
def test_the_customer_and_vendor_emails_show_the_vehicle_line(lang: str) -> None:
    order = place(vehicle=VEHICLE, language=lang)
    pay(order)
    notifications.notify_order_paid(order)
    confirmation = OutboxEmail.objects.get(template_key="orders.order_confirmation")
    rendered = core_services.render_email(
        confirmation.template_key, lang, confirmation.context_data
    )
    assert PHRASES[lang] in rendered.text_body
    assert PHRASES[lang] in rendered.html_body
    vendor_mail = OutboxEmail.objects.get(template_key="orders.vendor_new_order")
    assert vendor_mail.language == "en"  # vendors have no language of their own yet
    vendor = core_services.render_email("orders.vendor_new_order", "en", vendor_mail.context_data)
    assert PHRASES["en"] in vendor.text_body
    assert PHRASES["en"] in vendor.html_body


def test_an_order_without_a_vehicle_has_no_vehicle_line_in_the_emails() -> None:
    order = place()
    pay(order)
    notifications.notify_order_paid(order)
    for mail in OutboxEmail.objects.filter(
        template_key__in=("orders.order_confirmation", "orders.vendor_new_order")
    ):
        rendered = core_services.render_email(mail.template_key, "en", mail.context_data)
        assert "compatibility" not in rendered.text_body + rendered.html_body


def test_the_scrub_clears_the_vehicle() -> None:
    order = test_retention.place(created=test_retention.MID, vehicle=VEHICLE)
    from apps.orders import services

    services.restrict_order(order, ctx=test_retention.CTX)
    order.refresh_from_db()
    assert order.vehicle_snapshot == {}
