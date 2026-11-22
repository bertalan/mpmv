"""The "new trust rules version" form (TRU.1 part 2, slice 2). Validation of shape only: the
rules (ascending tiers, ranges, start date) stay in `apps.trust.engine` and `apps.trust.services`;
their messages are put back on the field they name."""

from decimal import Decimal
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.nfc_forms import NFCForm
from apps.trust.engine import TrustRulesData

from .trust_views import GROUPS

_WHOLE = {"days", "count", "characters", "stars"}


def plain(value: Decimal, unit: str) -> str:
    """A decimal as it is typed in the form: no exponent, two places for an amount."""
    return f"{value:.2f}" if unit == "amount" else format(value.normalize(), "f")


class TiersField(forms.CharField):
    """Whole numbers separated by commas, for example `10, 50, 100`."""

    def to_python(self, value: Any) -> list[int]:  # type: ignore[override]
        text = str(super().to_python(value) or "")
        try:
            return [int(part) for part in text.replace(";", ",").split(",") if part.strip()]
        except ValueError:
            raise forms.ValidationError(
                _("Write whole numbers separated by commas."), code="invalid"
            ) from None


class TrustRulesForm(NFCForm):
    valid_from = forms.DateTimeField(
        label=_("Valid from"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        help_text=_("The new version starts then and the one in force ends. Not in the past."),
    )
    note = forms.CharField(label=_("Note"), max_length=200, required=False)

    def __init__(self, *args: Any, current: TrustRulesData, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.units: dict[str, str] = {}
        for _heading, rows in GROUPS:
            for name, label, unit in rows:
                self.units[name] = unit
                value = getattr(current, name)
                field: forms.Field
                if unit == "tiers":
                    field = TiersField(label=label, initial=", ".join(str(v) for v in value))
                elif unit in _WHOLE:
                    field = forms.IntegerField(label=label, initial=value)
                else:
                    field = forms.DecimalField(
                        label=label, max_digits=8, decimal_places=2, initial=plain(value, unit)
                    )
                self.fields[name] = field

    def params(self) -> dict[str, Any]:
        """The cleaned thresholds as the service takes them (decimals as strings)."""
        return {
            name: str(self.cleaned_data[name])
            if isinstance(self.fields[name], forms.DecimalField)
            else self.cleaned_data[name]
            for name in self.units
        }

    def groups(self) -> list[tuple[Any, list[forms.BoundField]]]:
        return [(heading, [self[name] for name, _l, _u in rows]) for heading, rows in GROUPS]
