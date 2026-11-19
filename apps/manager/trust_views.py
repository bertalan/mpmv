"""Manager: the trust rules versions, read only (TRU.1 part 2, slice 1).

Platform space, section `platform.trust_rules` (CORE.18: configuration role and the platform
owner). The list shows every version with its period and a marker for the one in force; the
detail page shows the thresholds with plain labels, grouped by badge. Thin adapters over
`apps.trust`; the "new version" form with its dry-run preview comes in the next slice.
"""

import datetime as dt
from decimal import Decimal
from typing import Any, Final

from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.trust.models import TrustRules

from .views import ManagerView

SECTION: Final = "platform.trust_rules"  # a row of roles.MATRIX: configuration, owner

#: (badge heading, ((parameter name, label, unit), ...)) in the order of the spec 16 §16.4 table.
#: The unit is "days", "percent", "amount", "count", "characters", "stars", "tiers" or "".
GROUPS: Final = (
    (
        gettext_lazy("Orders delivered"),
        (
            ("orders_tiers", gettext_lazy("Order count tiers"), "tiers"),
            ("min_order_value", gettext_lazy("Smallest order value that counts"), "amount"),
            ("withdrawal_window_days", gettext_lazy("Withdrawal window"), "days"),
            ("orders_per_buyer_cap", gettext_lazy("Orders counted per buyer"), "count"),
            ("orders_cap_window_days", gettext_lazy("Window of the per-buyer cap"), "days"),
        ),
    ),
    (
        gettext_lazy("Reviews"),
        (
            ("reviews_stars_from", gettext_lazy("Stars from which a review is shown"), "stars"),
            ("review_excerpt_length", gettext_lazy("Review excerpt length"), "characters"),
        ),
    ),
    (
        gettext_lazy("Countries served"),
        (
            ("countries_min_orders", gettext_lazy("Orders needed per country"), "count"),
            ("countries_min_buyers", gettext_lazy("Buyers needed per country"), "count"),
        ),
    ),
    (
        gettext_lazy("Repeat customers"),
        (
            ("repeat_window_days", gettext_lazy("Window"), "days"),
            ("repeat_min_gap_days", gettext_lazy("Smallest gap between two orders"), "days"),
            ("repeat_shown_from", gettext_lazy("Shown from this many repeat buyers"), "count"),
        ),
    ),
    (
        gettext_lazy("Dispatch on time"),
        (
            ("dispatch_window_days", gettext_lazy("Window"), "days"),
            ("dispatch_min_orders", gettext_lazy("Orders needed"), "count"),
            ("dispatch_earn_percent", gettext_lazy("Earned at"), "percent"),
            ("dispatch_lose_percent", gettext_lazy("Lost below"), "percent"),
        ),
    ),
    (
        gettext_lazy("Disputes"),
        (
            ("dispute_window_days", gettext_lazy("Window"), "days"),
            ("dispute_min_orders", gettext_lazy("Orders needed"), "count"),
            ("dispute_earn_percent", gettext_lazy("Earned at or below"), "percent"),
            ("dispute_lose_percent", gettext_lazy("Lost above"), "percent"),
        ),
    ),
)


def format_value(value: Any, unit: str) -> str:
    """A threshold as staff read it."""
    if unit == "tiers":
        return ", ".join(str(v) for v in value)
    if unit == "percent":
        return f"{Decimal(value).normalize():f}%"
    if unit == "amount":
        return f"{Decimal(value):.2f}"
    if unit == "days":
        return _("%(n)s days") % {"n": value}
    if unit == "characters":
        return _("%(n)s characters") % {"n": value}
    return str(value)


def parameter_groups(rules: TrustRules) -> list[dict[str, Any]]:
    """The version's thresholds (spec defaults for a name the row omits), grouped by badge."""
    data = rules.to_data()
    return [
        {
            "heading": heading,
            "rows": [
                {"name": name, "label": label, "value": format_value(getattr(data, name), unit)}
                for name, label, unit in rows
            ],
        }
        for heading, rows in GROUPS
    ]


def in_force(rules: TrustRules, now: dt.datetime) -> bool:
    return rules.valid_from <= now and (rules.valid_to is None or rules.valid_to > now)


class TrustRulesListView(ManagerView):
    section = SECTION
    template_name = "manager/platform/trust_rules.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        context["new_url"] = reverse("manager:platform:trust_rule_new")
        context["versions"] = [
            {"rules": rules, "in_force": in_force(rules, now)}
            for rules in TrustRules.objects.order_by("-version")
        ]
        return context


class TrustRulesDetailView(ManagerView):
    section = SECTION
    template_name = "manager/platform/trust_rules_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        rules = TrustRules.objects.filter(version=kwargs["version"]).first()
        if rules is None:
            raise Http404("No such version.")
        context.update(
            rules=rules,
            in_force=in_force(rules, timezone.now()),
            groups=parameter_groups(rules),
            title=_("Trust rules, version %(n)s") % {"n": rules.version},
        )
        return context
