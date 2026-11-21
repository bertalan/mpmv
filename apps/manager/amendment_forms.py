"""The order-amendment forms of the Manager (ORD.20 part 3): input only, the services decide.

`AmendmentForm` turns what the vendor (or platform staff) typed into the typed changes of
`apps.orders.amendment_engine`; the service recomputes, classifies and refuses with a plain
reason. Platform staff get no line fields: they never change products, quantities or prices
(spec 07, Roles and the passkey), and the service refuses it too.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.nfc_forms import NFCForm
from apps.orders import amendment_engine as am

ADDRESS_FIELDS = ("line1", "line2", "city", "postcode", "state", "country")
REQUEST_CHANNELS = (
    ("email", _("Email")),
    ("phone", _("Phone call")),
    ("chat", _("Chat or message")),
    ("in_person", _("In person")),
    ("other", _("Other")),
)


@dataclass(frozen=True)
class LineChoice:
    """A line of the current version as the form shows it."""

    key: str
    title: str
    quantity: int
    unit_price_net: Decimal


def lines_of(snapshot: Mapping[str, Any]) -> list[LineChoice]:
    return [
        LineChoice(
            key=str(line["line_key"]),
            title=str(line["title"])
            + (f" ({line['variant_label']})" if line.get("variant_label") else ""),
            quantity=int(line["quantity"]),
            unit_price_net=Decimal(str(line["unit_price_net"])),
        )
        for line in snapshot.get("lines", [])
    ]


class AmendmentForm(NFCForm):
    """One draft: line changes (shop only), a carrier, a customer-requested address correction,
    the reason and, for platform staff, the mandatory note."""

    nfc_exact_fields = ("carrier", "address_country")  # matched byte for byte (I18N.10)

    carrier = forms.ChoiceField(
        label=_("Shipping method"),
        required=False,
        help_text=_("Leave it as it is to keep the current method."),
    )
    change_address = forms.BooleanField(
        label=_("Correct the delivery address, as the customer asked"),
        required=False,
        help_text=_(
            "Only on the customer's request, inside the same country and tax territory. Record "
            "how and when the customer asked, and their words."
        ),
    )
    address_line1 = forms.CharField(label=_("Address"), max_length=200, required=False)
    address_line2 = forms.CharField(label=_("Address, second line"), max_length=200, required=False)
    address_city = forms.CharField(label=_("City"), max_length=100, required=False)
    address_postcode = forms.CharField(label=_("Postcode"), max_length=20, required=False)
    address_state = forms.CharField(label=_("Province or state"), max_length=100, required=False)
    address_country = forms.CharField(
        label=_("Country"),
        max_length=2,
        required=False,
        help_text=_("Two letters, for example IT."),
    )
    request_channel = forms.ChoiceField(
        label=_("How the customer asked"),
        required=False,
        choices=[("", "---------"), *REQUEST_CHANNELS],
    )
    request_date = forms.DateField(
        label=_("When the customer asked"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    request_text = forms.CharField(
        label=_("The customer's words"),
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    reason = forms.CharField(
        label=_("Reason for the change"),
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("The customer reads it. Write it in plain words."),
    )

    def __init__(
        self,
        *args: Any,
        lines: Sequence[LineChoice] = (),
        carriers: Sequence[am.ShippingOffer] = (),
        current_carrier: str = "",
        address: Mapping[str, Any] | None = None,
        staff: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.staff = staff
        self.lines = list(lines) if not staff else []
        self.address = dict(address or {})
        carrier_field: forms.ChoiceField = self.fields["carrier"]  # type: ignore[assignment]
        carrier_field.choices = [
            ("", _("No change")),
            *[
                (
                    offer.key,
                    f"{offer.label or offer.key}, {offer.net} EUR"
                    + (f", {offer.max_days} d" if offer.max_days is not None else ""),
                )
                for offer in carriers
                if offer.key != current_carrier
            ],
        ]
        if not self.is_bound:
            for name in ADDRESS_FIELDS:
                self.initial.setdefault(f"address_{name}", self.address.get(name, ""))
        for line in self.lines:
            prefix = f"line_{line.key}"
            self.fields[f"{prefix}_remove"] = forms.BooleanField(
                label=_("Remove %(title)s") % {"title": line.title}, required=False
            )
            self.fields[f"{prefix}_quantity"] = forms.IntegerField(
                label=_("New quantity of %(title)s (now %(quantity)s)")
                % {"title": line.title, "quantity": line.quantity},
                required=False,
                min_value=1,
            )
            self.fields[f"{prefix}_price"] = forms.DecimalField(
                label=_("New unit price of %(title)s, EUR net of VAT (now %(price)s)")
                % {"title": line.title, "price": line.unit_price_net},
                required=False,
                min_value=Decimal("0.01"),
                max_digits=12,
                decimal_places=2,
            )
            self.fields[f"{prefix}_unavailable"] = forms.BooleanField(
                label=_("The removed units of %(title)s are not available") % {"title": line.title},
                required=False,
                help_text=_(
                    "Tick it when the goods are missing: no stock comes back. Leave it empty "
                    "when you take them out for another reason: the units go back to stock."
                ),
            )
        if staff:
            self.fields["staff_note"] = forms.CharField(
                label=_("Staff note"),
                max_length=2000,
                widget=forms.Textarea(attrs={"rows": 3}),
                help_text=_("Required for platform staff. The customer does not see it."),
            )

    def line_fields(self) -> list[dict[str, Any]]:
        """The bound fields of each line, grouped, for the template."""
        return [
            {
                "line": line,
                "fields": [
                    self[f"line_{line.key}_{name}"]
                    for name in ("remove", "quantity", "price", "unavailable")
                ],
            }
            for line in self.lines
        ]

    def other_fields(self) -> list[forms.BoundField]:
        return [
            field
            for field in self.visible_fields()
            if not field.name.startswith("line_") and field.name not in {"reason", "staff_note"}
        ]

    def closing_fields(self) -> list[forms.BoundField]:
        return [self[name] for name in ("reason", "staff_note") if name in self.fields]

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("change_address"):
            if not str(data.get("address_line1") or "").strip():
                self.add_error("address_line1", _("Enter the corrected address."))
            for name in ("request_channel", "request_date", "request_text"):
                if not data.get(name):
                    self.add_error(
                        name, _("Record how, when and in which words the customer asked.")
                    )
        for line in self.lines:
            prefix = f"line_{line.key}"
            quantity = data.get(f"{prefix}_quantity")
            if data.get(f"{prefix}_remove") and quantity is not None:
                self.add_error(
                    f"{prefix}_quantity",
                    _("Either remove the line or change its quantity, not both."),
                )
        if not self.errors and not self.changes():
            raise forms.ValidationError(_("Choose at least one change."))
        return data

    def changes(self) -> list[am.AmendmentChange]:
        data = self.cleaned_data
        found: list[am.AmendmentChange] = []
        for line in self.lines:
            prefix = f"line_{line.key}"
            unavailable = bool(data.get(f"{prefix}_unavailable"))
            quantity = data.get(f"{prefix}_quantity")
            price = data.get(f"{prefix}_price")
            if data.get(f"{prefix}_remove"):
                found.append(am.RemoveLine(line.key, unavailable=unavailable))
                continue
            if quantity is not None and quantity != line.quantity:
                found.append(am.ChangeQuantity(line.key, quantity, unavailable=unavailable))
            if price is not None and price != line.unit_price_net:
                found.append(am.ChangePrice(line.key, price))
        carrier = data.get("carrier")
        if carrier:
            found.append(am.ChangeCarrier(am.ShippingOffer(carrier, "", Decimal("0"))))
        if data.get("change_address"):
            address = dict(self.address)
            for name in ADDRESS_FIELDS:
                value = str(data.get(f"address_{name}") or "").strip()
                if name == "country":
                    value = value.upper()
                if value or name in address:
                    address[name] = value
            found.append(am.ChangeAddress(address=address, requested_by_customer=True))
        return found

    def customer_request(self) -> dict[str, str] | None:
        data = self.cleaned_data
        if not data.get("change_address"):
            return None
        when = data.get("request_date")
        return {
            "channel": str(data.get("request_channel") or ""),
            "date": when.isoformat() if isinstance(when, dt.date) else "",
            "text": str(data.get("request_text") or ""),
        }


def initial_from(
    changes: Sequence[Mapping[str, Any]], request: Mapping[str, Any]
) -> dict[str, Any]:
    """The form's initial values for an open draft, so a revision starts from it."""
    initial: dict[str, Any] = {}
    for raw in changes:
        change = am.change_from_json(raw)
        match change:
            case am.RemoveLine(line_key=key, unavailable=unavailable):
                initial[f"line_{key}_remove"] = True
                initial[f"line_{key}_unavailable"] = unavailable
            case am.ChangeQuantity(line_key=key, quantity=quantity, unavailable=unavailable):
                initial[f"line_{key}_quantity"] = quantity
                initial[f"line_{key}_unavailable"] = unavailable
            case am.ChangePrice(line_key=key, unit_price_net=price):
                initial[f"line_{key}_price"] = price
            case am.ChangeCarrier(offer=offer):
                initial["carrier"] = offer.key
            case am.ChangeAddress(address=address):
                initial["change_address"] = True
                for name in ADDRESS_FIELDS:
                    initial[f"address_{name}"] = address.get(name, "")
                initial["request_channel"] = request.get("channel", "")
                initial["request_date"] = request.get("date", "")
                initial["request_text"] = request.get("text", "")
            case _:
                pass
    return initial


class CancelPackageForm(NFCForm):
    """Cancel the whole package with a full refund (platform staff: the cause and the note are
    required; the cause decides who pays the refund fee, owner decision 2026-11-16)."""

    cause = forms.ChoiceField(
        label=_("Why the package is cancelled"),
        widget=forms.RadioSelect,
        choices=[
            (
                "vendor_fault",
                _(
                    "The shop's fault, for example the goods are unavailable or the shop cannot "
                    "fulfil the order: the refund fee is charged to the shop"
                ),
            ),
            (
                "customer_request",
                _("The customer asked to cancel: no refund fee for the shop"),
            ),
            ("platform_error", _("A platform error: no refund fee for the shop")),
        ],
    )

    reason = forms.CharField(
        label=_("Reason for the cancellation"),
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("The customer reads it. Write it in plain words."),
    )
    staff_note = forms.CharField(
        label=_("Staff note"),
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Required for platform staff. The customer does not see it."),
    )


class ShopCancelForm(NFCForm):
    """The shop cancels the whole package with a full refund: for its own fault (the refund fee is
    the shop's), or because the customer asked, recorded with how, when and in which words (no
    refund fee)."""

    cause = forms.ChoiceField(
        label=_("Why the package is cancelled"),
        widget=forms.RadioSelect,
        choices=[
            (
                "vendor_fault",
                _(
                    "We cannot fulfil it, for example the goods are unavailable: the refund fee "
                    "is charged to the shop"
                ),
            ),
            (
                "customer_request",
                _("The customer asked to cancel: no refund fee; record the request below"),
            ),
        ],
    )
    request_channel = forms.ChoiceField(
        label=_("How the customer asked"),
        required=False,
        choices=[("", "---------"), *REQUEST_CHANNELS],
    )
    request_date = forms.DateField(
        label=_("When the customer asked"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    request_text = forms.CharField(
        label=_("The customer's words"),
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    reason = forms.CharField(
        label=_("Reason for the cancellation"),
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("The customer reads it. Write it in plain words."),
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("cause") == "customer_request":
            for name in ("request_channel", "request_date", "request_text"):
                if not data.get(name):
                    self.add_error(
                        name, _("Record how, when and in which words the customer asked.")
                    )
        return data

    def customer_request(self) -> dict[str, str] | None:
        data = self.cleaned_data
        if data.get("cause") != "customer_request":
            return None
        when = data.get("request_date")
        return {
            "channel": str(data.get("request_channel") or ""),
            "date": when.isoformat() if isinstance(when, dt.date) else "",
            "text": str(data.get("request_text") or ""),
        }
