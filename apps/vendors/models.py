"""Vendors, their legal identity and their per-language storefront texts and policies (spec 04
§4.2, VEN.1, VEN.12).

Fields owned by later tasks are added there: `plan` (VEN.2), `payout_account` (VEN.6),
`dac7_complete` (CMP.1), `logo` (media/images). Storefront texts follow the shared translation
workflow (`apps.core.models.TranslationModel`, I18N.2). Which legal fields are required, and for
what, is data: `LegalFieldRequirement` (VEN.12).
"""

import dataclasses
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.fields import CountryField, MoneyField
from apps.core.models import BaseModel, TranslationModel, resolve_translation
from apps.core.translations import Resolved

from . import engine


def validate_registered_address(value: Any) -> None:
    """Validated the JSON address that VEN.12 replaced with structured fields.

    Nothing uses it any more; it stays importable because migration 0001 references it.
    """


class VendorStatus(models.TextChoices):
    APPLICANT = engine.APPLICANT, "Applicant"
    ONBOARDING = engine.ONBOARDING, "Onboarding"
    ACTIVE = engine.ACTIVE, "Active"
    SUSPENDED = engine.SUSPENDED, "Suspended"
    CLOSED = engine.CLOSED, "Closed"


class VendorSalesMode(models.TextChoices):
    BUY = "buy", "Buy"
    ON_REQUEST = "on_request", "On request"


class LegalForm(models.TextChoices):
    SOLE_PROPRIETORSHIP = engine.SOLE_PROPRIETORSHIP, "Sole proprietorship (ditta individuale)"
    PARTNERSHIP = engine.PARTNERSHIP, "Partnership (S.n.c., S.a.s.)"
    SRL = engine.SRL, "Limited liability company (S.r.l.)"
    SPA = engine.SPA, "Joint-stock company (S.p.A.)"
    COOPERATIVE = engine.COOPERATIVE, "Cooperative"
    OTHER = engine.OTHER_LEGAL_FORM, "Other, or a foreign legal form"


def _regime(code: str) -> tuple[str, str]:
    return code, f"{code} - {engine.FISCAL_REGIMES[code]}"


class FiscalRegime(models.TextChoices):
    """FatturaPA `RegimeFiscale`, with the official SDI labels (`engine.FISCAL_REGIMES`)."""

    RF01 = _regime("RF01")
    RF02 = _regime("RF02")
    RF04 = _regime("RF04")
    RF05 = _regime("RF05")
    RF06 = _regime("RF06")
    RF07 = _regime("RF07")
    RF08 = _regime("RF08")
    RF09 = _regime("RF09")
    RF10 = _regime("RF10")
    RF11 = _regime("RF11")
    RF12 = _regime("RF12")
    RF13 = _regime("RF13")
    RF14 = _regime("RF14")
    RF15 = _regime("RF15")
    RF16 = _regime("RF16")
    RF17 = _regime("RF17")
    RF18 = _regime("RF18")
    RF19 = _regime("RF19")


class SoleShareholder(models.TextChoices):
    SU = engine.SOLE_SHAREHOLDER, "SU - sole shareholder"
    SM = engine.SEVERAL_SHAREHOLDERS, "SM - several shareholders"


class LiquidationStatus(models.TextChoices):
    LN = engine.NOT_IN_LIQUIDATION, "LN - not in liquidation"
    LS = engine.IN_LIQUIDATION, "LS - in liquidation"


class ViesStatus(models.TextChoices):
    UNCHECKED = engine.VIES_UNCHECKED, "Not checked"
    VALID = engine.VIES_VALID, "Valid in VIES"
    INVALID = engine.VIES_INVALID, "Invalid in VIES"
    UNAVAILABLE = engine.VIES_UNAVAILABLE, "VIES unavailable"


PROVINCE_CHOICES = [(code, code) for code in sorted(engine.PROVINCE_CODES)]


PROVINCE_HELP = "Italy: the 2-letter province code (e.g. RM). Elsewhere: state or region."


class Vendor(BaseModel):
    Status = VendorStatus
    SalesMode = VendorSalesMode
    LegalForm = LegalForm
    FiscalRegime = FiscalRegime
    ViesStatus = ViesStatus

    legal_name = models.CharField(max_length=200)
    display_name = models.CharField(max_length=150)
    slug = models.SlugField(
        max_length=engine.SLUG_MAX_LENGTH,
        unique=True,
        blank=True,
        help_text="Leave empty to generate it from the display name. Changing it breaks URLs.",
    )
    # Changed only through `services.change_status`, which checks the state machine and audits.
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.APPLICANT, editable=False
    )
    country = CountryField(help_text="Country of establishment (ISO 3166-1 alpha-2).")
    contact_email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    sales_mode_default = models.CharField(
        max_length=16, choices=SalesMode.choices, default=SalesMode.BUY
    )
    enquiries_enabled = models.BooleanField(default=False)
    legacy_wp_user_id = models.PositiveBigIntegerField(
        null=True, blank=True, unique=True, help_text="Vendor user id in the legacy system."
    )

    # --- Legal and e-invoicing identity (VEN.12). Every field is optional while the profile is
    # edited; `LegalFieldRequirement` says which ones a payout needs. Values that are present
    # are validated by `engine.identity_errors` (see `clean_fields`).
    legal_form = models.CharField(max_length=24, choices=LegalForm.choices, blank=True)
    first_name = models.CharField(
        max_length=60, blank=True, help_text="Sole proprietors: first name (FatturaPA Nome)."
    )
    last_name = models.CharField(
        max_length=60, blank=True, help_text="Sole proprietors: last name (FatturaPA Cognome)."
    )
    vat_number = models.CharField("VAT number", max_length=32, blank=True)
    # Written by `services.check_vat_number` only; reset when the VAT number changes.
    vies_status = models.CharField(
        "VIES status",
        max_length=16,
        choices=ViesStatus.choices,
        default=ViesStatus.UNCHECKED,
        editable=False,
    )
    vies_checked_at = models.DateTimeField("VIES checked at", null=True, blank=True, editable=False)
    tax_code = models.CharField(
        "tax code (codice fiscale)",
        max_length=32,
        blank=True,
        help_text="Italy: 16 characters, or 11 digits for entities. Elsewhere: the national TIN.",
    )
    fiscal_regime = models.CharField(
        max_length=4,
        choices=FiscalRegime.choices,
        blank=True,
        help_text="The regime declared on invoices (FatturaPA RegimeFiscale; RF19 = forfettario).",
    )
    ateco_code = models.CharField("ATECO code", max_length=8, blank=True)
    eori = models.CharField("EORI number", max_length=17, blank=True)
    registered_street = models.CharField("registered office street", max_length=200, blank=True)
    registered_street_number = models.CharField(
        "registered office street number", max_length=16, blank=True
    )
    registered_postal_code = models.CharField(
        "registered office postal code", max_length=16, blank=True
    )
    registered_city = models.CharField("registered office city", max_length=100, blank=True)
    registered_province = models.CharField(
        "registered office province", max_length=64, blank=True, help_text=PROVINCE_HELP
    )
    registered_country = CountryField("registered office country", blank=True)
    rea_office = models.CharField("REA office", max_length=2, choices=PROVINCE_CHOICES, blank=True)
    rea_number = models.CharField("REA number", max_length=20, blank=True)
    share_capital = MoneyField(null=True, blank=True)
    sole_shareholder = models.CharField(max_length=2, choices=SoleShareholder.choices, blank=True)
    liquidation_status = models.CharField(
        max_length=2, choices=LiquidationStatus.choices, blank=True
    )
    sdi_recipient_code = models.CharField(
        "SDI recipient code",
        max_length=7,
        blank=True,
        help_text=(
            f"7 characters; {engine.SDI_PEC_CODE} when invoices go to the PEC address, "
            f"{engine.SDI_FOREIGN_CODE} for vendors established outside Italy."
        ),
    )
    pec = models.EmailField("PEC address", blank=True, help_text="Certified email (PEC).")
    fiscal_representative_name = models.CharField(
        max_length=80, blank=True, help_text="Non-EU vendors: the fiscal representative in Italy."
    )
    fiscal_representative_vat_number = models.CharField(
        "fiscal representative VAT number", max_length=16, blank=True
    )
    establishment_street = models.CharField(
        "permanent establishment street", max_length=200, blank=True
    )
    establishment_street_number = models.CharField(
        "permanent establishment street number", max_length=16, blank=True
    )
    establishment_postal_code = models.CharField(
        "permanent establishment postal code", max_length=16, blank=True
    )
    establishment_city = models.CharField(
        "permanent establishment city", max_length=100, blank=True
    )
    establishment_province = models.CharField(
        "permanent establishment province", max_length=64, blank=True, help_text=PROVINCE_HELP
    )
    establishment_country = CountryField("permanent establishment country", blank=True)
    # SEC.3: to be encrypted at rest (field-level encryption). Audit rows mask it already.
    iban = models.CharField("IBAN", max_length=34, blank=True)
    bic = models.CharField("BIC", max_length=11, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)

    _stored_vat_number: str | None = None  # VAT number as loaded, to reset the VIES status

    class Meta:
        ordering = ["display_name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=VendorStatus.values),
                name="vendors_vendor_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(sales_mode_default__in=VendorSalesMode.values),
                name="vendors_vendor_sales_mode_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(legal_form__in=["", *LegalForm.values]),
                name="vendors_vendor_legal_form_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(fiscal_regime__in=["", *FiscalRegime.values]),
                name="vendors_vendor_fiscal_regime_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(vies_status__in=ViesStatus.values),
                name="vendors_vendor_vies_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name

    @classmethod
    def from_db(cls, db: str | None, field_names: Any, values: Any, **kwargs: Any) -> "Vendor":
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._stored_vat_number = instance.__dict__.get("vat_number")  # None if deferred
        return instance

    def translation(self, language: str) -> "Resolved[VendorTranslation]":
        """Storefront texts to show in `language`: published, or EN with a fallback flag."""
        return resolve_translation(self.translations.all(), language)

    def legal_identity(self) -> engine.LegalIdentity:
        """The legal identity as plain values, for the pure rules in `engine`."""
        names = ("country", "legal_name", "contact_email", "phone", *engine.LEGAL_FIELDS)
        return engine.LegalIdentity(**{name: getattr(self, name) for name in names})

    def clean_fields(self, exclude: Any = None) -> None:
        """Field validation, then the legal identity rules on the values that are present."""
        excluded = set(exclude or ())
        errors: dict[str, Any] = {}
        try:
            super().clean_fields(exclude=exclude)
        except ValidationError as exc:
            errors = exc.update_error_dict(errors)
        normalized = engine.normalized(self.legal_identity())
        for name in (
            *engine.COMPACT_FIELDS,
            "pec",
            "registered_province",
            "establishment_province",
        ):
            if name not in excluded and name not in errors:
                setattr(self, name, getattr(normalized, name))
        identity = self.legal_identity()
        if "share_capital" in errors:  # not a number: leave it to the field error
            identity = dataclasses.replace(identity, share_capital=None)
        for name, messages in engine.identity_errors(identity).items():
            if name not in excluded and name not in errors:
                errors[name] = messages
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = engine.unique_slug(self.display_name, taken=_TakenSlugs(self.pk))
        update_fields = kwargs.get("update_fields")
        vat_saved = update_fields is None or "vat_number" in update_fields
        if (
            vat_saved
            and self._stored_vat_number is not None
            and self.vat_number != self._stored_vat_number
        ):
            # A new VAT number has not been checked in VIES yet.
            self.vies_status = ViesStatus.UNCHECKED
            self.vies_checked_at = None
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "vies_status", "vies_checked_at"}
        super().save(*args, **kwargs)
        if vat_saved:
            self._stored_vat_number = self.vat_number


class _TakenSlugs:
    """`slug in taken` asks the database, so `engine.unique_slug` stays pure."""

    def __init__(self, exclude_pk: int | None) -> None:
        self.exclude_pk = exclude_pk

    def __contains__(self, slug: object) -> bool:
        others = Vendor.objects.all()
        if self.exclude_pk is not None:
            others = others.exclude(pk=self.exclude_pk)
        return others.filter(slug=slug).exists()


class VendorTranslation(TranslationModel):
    """A vendor's storefront texts and policies in one language (explicit table, spec 09)."""

    translation_of = "vendor"
    translated_fields = (
        "tagline",
        "description",
        "shipping_policy",
        "refund_policy",
        "cancellation_policy",
    )

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="translations")
    tagline = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    shipping_policy = models.TextField(blank=True)
    refund_policy = models.TextField(blank=True)
    cancellation_policy = models.TextField(blank=True)

    class Meta(TranslationModel.Meta):
        ordering = ["vendor", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["vendor", "language"], name="vendors_translation_vendor_language_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} [{self.language}]"


class VendorMemberRole(models.TextChoices):
    OWNER = engine.OWNER, "Owner"
    STAFF = engine.STAFF, "Staff"


class VendorMember(BaseModel):
    """A user's membership of a vendor, with a role (spec 02 §2.2, CORE.2).

    Created, re-roled and removed only through `services.add_member`, `change_member_role` and
    `remove_member`, which keep at least one owner per vendor and write the audit log. What a
    role may do is the matrix in `engine.ROLE_PERMISSIONS`.
    """

    Role = VendorMemberRole

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="members")
    # PROTECT: a membership disappears through `remove_member` (last-owner rule, audit), never as
    # a side effect of deleting the user.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vendor_memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices, editable=False)

    class Meta:
        ordering = ["vendor", "role", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "user"], name="vendors_member_vendor_user_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=VendorMemberRole.values),
                name="vendors_member_role_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.vendor} ({self.role})"


class RequirementPurpose(models.TextChoices):
    PAYOUT = engine.PAYOUT, "Payout request"


def legal_field_label(name: str) -> str:
    """The verbose name of a vendor legal field, e.g. "PEC address" for `pec`."""
    return str(getattr(Vendor._meta.get_field(name), "verbose_name", name))


LEGAL_FIELD_CHOICES = [
    (name, legal_field_label(name)[:1].upper() + legal_field_label(name)[1:])
    for name in engine.LEGAL_FIELDS
]


class LegalFieldRequirement(BaseModel):
    """A vendor legal field that a purpose (today: a payout request) requires (VEN.12).

    Staff-editable data, not code: changes go through `services.save_requirement` and
    `delete_requirement`, which audit them. Rules are additive (`engine.required_fields`): every
    active row matching the vendor's country and legal form applies. An empty country or legal
    form matches every vendor.
    """

    Purpose = RequirementPurpose

    country = CountryField(blank=True, help_text="Empty: every country.")
    legal_form = models.CharField(
        max_length=24, choices=LegalForm.choices, blank=True, help_text="Empty: every legal form."
    )
    field_name = models.CharField("field", max_length=64, choices=LEGAL_FIELD_CHOICES)
    purpose = models.CharField(max_length=16, choices=Purpose.choices, default=Purpose.PAYOUT)
    active = models.BooleanField(default=True, help_text="Inactive rules are ignored.")
    note = models.CharField(max_length=200, blank=True, help_text="Why the field is required.")

    class Meta:
        ordering = ["purpose", "country", "legal_form", "field_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["country", "legal_form", "field_name", "purpose"],
                name="vendors_requirement_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(field_name__in=engine.LEGAL_FIELDS),
                name="vendors_requirement_field_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(purpose__in=RequirementPurpose.values),
                name="vendors_requirement_purpose_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(legal_form__in=["", *LegalForm.values]),
                name="vendors_requirement_legal_form_valid",
            ),
        ]

    def __str__(self) -> str:
        scope = " ".join(filter(None, [self.country, self.legal_form])) or "all vendors"
        return f"{self.field_name} for {self.purpose} ({scope})"

    def as_rule(self) -> engine.Requirement:
        return engine.Requirement(
            field=self.field_name,
            country=self.country,
            legal_form=self.legal_form,
            purpose=self.purpose,
        )
