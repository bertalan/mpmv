"""Trust rules and the vendor trust snapshots (TRU.1, spec 16 §16.4).

`TrustRules` is versioned configuration like `CommissionRule`: a change is a new row valid from a
date, and a database trigger refuses any edit or delete (only closing `valid_to` once). A
`VendorTrustSnapshot` is the stored result of `engine.compute_vendor_trust`, append-only with a
database trigger, and keeps the `rules_version` it was computed with so staff can explain any
result later (§16.5 point 7).
"""

from typing import Any, ClassVar

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models

from apps.core.models import BaseModel, TsTzRange, Versioned, valid_period_check
from apps.vendors.models import Vendor
from apps.vendors.scoping import VendorScopedManager

from . import engine


class TrustRules(Versioned):
    """One version of the trust thresholds, valid over `[valid_from, valid_to)`.

    `params` holds the `engine.TrustRulesData` fields other than the version (decimals as
    strings); a missing name takes the spec default. Written only by `services.add_trust_rules`.
    """

    version = models.PositiveIntegerField(
        unique=True, editable=False, help_text="Counts up from 1."
    )
    params = models.JSONField(
        default=dict, help_text="Thresholds and tiers; see the trust rules page."
    )
    note = models.CharField(max_length=200, blank=True, help_text="Why the rules changed.")

    class Meta:
        ordering = ["-version"]
        verbose_name_plural = "trust rules"
        constraints = [
            valid_period_check("trust_trustrules_valid_to_after_valid_from"),
            ExclusionConstraint(
                name="trust_trustrules_no_overlap",
                expressions=[(TsTzRange("valid_from", "valid_to"), RangeOperators.OVERLAPS)],
                violation_error_message="Trust rules periods must not overlap.",
            ),
        ]

    def __str__(self) -> str:
        return f"Trust rules v{self.version} from {self.valid_from:%Y-%m-%d}"

    def to_data(self) -> engine.TrustRulesData:
        return engine.rules_from_params(self.version, self.params)


class VendorTrustSnapshot(BaseModel):
    """The trust result of one vendor at `as_of`, computed under `rules` (never edited).

    `badges` is `BadgeResult.to_json()` per badge: the earned flag, tier, the figures and, for
    a missing badge, the reason. Public pages read only the latest snapshot of a vendor.
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="trust_snapshots")
    as_of = models.DateTimeField()
    rules = models.ForeignKey(TrustRules, on_delete=models.PROTECT, related_name="snapshots")
    rules_version = models.PositiveIntegerField(
        help_text="Kept apart from the rules row so the result stays explainable."
    )
    metrics = models.JSONField(default=dict)
    badges = models.JSONField(default=list)

    class Meta:
        ordering = ["-as_of", "-id"]
        indexes = [models.Index(fields=["vendor", "-as_of"], name="trust_snapshot_vendor_asof")]

    def __str__(self) -> str:
        return f"{self.vendor} trust at {self.as_of:%Y-%m-%d} (rules v{self.rules_version})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("VendorTrustSnapshot is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("VendorTrustSnapshot is append-only")
