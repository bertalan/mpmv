"""Vendors, their legal identity and their per-language storefront texts and policies (spec 04
§4.2, VEN.1, VEN.12).

Fields owned by later tasks are added there: `plan` (VEN.19: the per-vendor plan assignment with
its effective-dated history; VEN.2 only defines `VendorPlan` and the rules),
`payout_account` (VEN.6),
`dac7_complete` (CMP.1), `logo` (media/images). Storefront texts follow the shared translation
workflow (`apps.core.models.TranslationModel`, I18N.2). Which legal fields are required, and for
what, is data: `LegalFieldRequirement` (VEN.12). The versioned vendor contract and its
append-only acceptances are VEN.5.
"""

import dataclasses
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Coalesce
from wagtail.fields import RichTextField

from apps.core.fields import (
    CountryField,
    EncryptedCharField,
    EncryptedDateField,
    LanguageField,
    MoneyField,
)
from apps.core.models import (
    VENDOR_OVERRIDES,
    BaseModel,
    FeatureFlag,
    TranslationModel,
    TsTzRange,
    Versioned,
    resolve_translation,
    valid_period_check,
)
from apps.core.translations import Resolved, source_hash

from . import engine
from .scoping import VendorScopedManager


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
    RF20 = _regime("RF20")


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

    # Tenant isolation (CORE.3): a vendor is its own tenant.
    vendor_scope: ClassVar[str] = "pk"
    objects = VendorScopedManager()

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
    # Raised by `services.reopen_vendor` (VEN.21): contract acceptances count for the current
    # round only, so a reopened vendor accepts the contract in force again.
    onboarding_round = models.PositiveIntegerField(
        default=1,
        editable=False,
        help_text="1 for the first onboarding, one more each time staff reopen the closed vendor.",
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
    # The storefront logo (CORE.9 part 2b). Written only by `services.set_vendor_logo` and
    # `remove_vendor_logo` (owner, audited), which store the image in the vendor's own Wagtail
    # collection `vendor-<public id>` through the SEC.2 upload checks.
    logo = models.ForeignKey(
        "core.MediaImage",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The vendor's logo; set by the owner in the Manager.",
    )
    logo_alt = models.CharField(
        max_length=125,
        blank=True,
        editable=False,
        help_text="Plain-text description of the logo for screen readers.",
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
    # DAC7 (VEN.23), sole proprietors: personal data, encrypted at rest and masked in the audit
    # log (SEC.3).
    date_of_birth = EncryptedDateField(
        null=True, blank=True, help_text="DAC7, sole proprietors: date of birth."
    )
    place_of_birth = EncryptedCharField(
        max_length=100,
        blank=True,
        help_text="DAC7, sole proprietors whose TIN country issues no TIN: city and country.",
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
    # SEC.3: encrypted at rest and masked in the audit log (for a sole proprietor it is a personal
    # identifier). The VAT number is not: it is public in VIES and staff search it.
    tax_code = EncryptedCharField(
        "tax code (codice fiscale) or TIN",
        max_length=32,
        blank=True,
        help_text="Italy: 16 characters, or 11 digits for entities. Elsewhere: the national TIN.",
    )
    tin_country = CountryField(
        "TIN issuing country",
        blank=True,
        help_text="DAC7: the country that issued the TIN (format checked for that country).",
    )
    tin_same_as_vat = models.BooleanField(
        "TIN same as the VAT number",
        default=False,
        help_text=(
            "Entities whose TIN is their VAT number: the TIN is filled from the VAT number once "
            "VIES says it is valid."
        ),
    )
    tax_residence_country = CountryField(
        "country of tax residence", blank=True, help_text="DAC7: the Member State of residence."
    )
    fiscal_regime = models.CharField(
        max_length=4,
        choices=FiscalRegime.choices,
        blank=True,
        help_text="The regime declared on invoices (FatturaPA RegimeFiscale; RF19 = forfettario).",
    )
    cross_border_franchise_number = models.CharField(
        "cross-border franchise number",
        max_length=20,
        blank=True,
        help_text=(
            "RF20 only: the number with the suffix EX issued by the member state of "
            "establishment for the cross-border VAT franchise (e.g. DE123456789EX)."
        ),
    )
    ateco_code = models.CharField("ATECO code", max_length=8, blank=True)
    eori = models.CharField("EORI number", max_length=17, blank=True)
    business_registration_number = models.CharField(
        "business registration number",
        max_length=40,
        blank=True,
        help_text="DAC7, entities: the company register number (in Italy, the REA number).",
    )
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
    # SEC.3: encrypted at rest and masked in the audit log.
    iban = EncryptedCharField("IBAN", max_length=34, blank=True)
    bic = models.CharField("BIC", max_length=11, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    account_holder_name = models.CharField(
        "account holder name",
        max_length=140,
        blank=True,
        help_text="DAC7: only when the account holder is not the vendor.",
    )

    _stored_vat_number: str | None = None  # VAT number as loaded, to reset the VIES status

    class Meta:
        ordering = ["display_name", "id"]
        permissions = [
            ("view_all_vendors", "Platform staff: can see every vendor's data (spec 02, Q-34)"),
        ]
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
        names = (
            "country",
            "legal_name",
            "contact_email",
            "phone",
            "tin_same_as_vat",
            "vies_status",
            *engine.LEGAL_FIELDS,
        )
        return engine.LegalIdentity(**{name: getattr(self, name) for name in names})

    def fill_tin_from_vat(self) -> None:
        """ "Same as the VAT number": derive the TIN from the VAT number, which counts as VIES
        valid only while it is the number VIES checked (a new one is checked again)."""
        identity = self.legal_identity()
        if self._state.adding or self.vat_number != self._stored_vat_number:
            identity = dataclasses.replace(identity, vies_status=engine.VIES_UNCHECKED)
        filled = engine.tin_from_vat(identity)
        self.tax_code, self.tin_country = filled.tax_code, filled.tin_country

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

    def normalize(self) -> None:
        """Fill what `save` would derive (the slug), so audits see stored values."""
        if not self.slug:
            self.slug = engine.unique_slug(self.display_name, taken=_TakenSlugs(self.pk))

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.normalize()
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
    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()
    translated_fields = (
        "tagline",
        "description",
        "shipping_policy",
        "refund_policy",
        "cancellation_policy",
    )

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="translations")
    tagline = models.CharField(max_length=200, blank=True)
    description = RichTextField(blank=True)
    shipping_policy = RichTextField(blank=True)
    refund_policy = RichTextField(blank=True)
    cancellation_policy = RichTextField(blank=True)

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
    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

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


class ClaimStatus(models.TextChoices):
    OPEN = engine.CLAIM_OPEN, "Link sent, waiting for the claimant"
    LOCKED = engine.CLAIM_LOCKED, "Locked after too many wrong answers"
    SUBMITTED = engine.CLAIM_SUBMITTED, "Submitted, waiting for approval"
    APPROVED = engine.CLAIM_APPROVED, "Approved"
    REJECTED = engine.CLAIM_REJECTED, "Rejected"


class ClaimChannel(models.TextChoices):
    EMAIL = engine.CLAIM_CHANNEL_EMAIL, "Legal email of record"
    PEC = engine.CLAIM_CHANNEL_PEC, "PEC address"


class VendorClaim(BaseModel):
    """The claim of a vendor imported without an account holder (VEN.24, owner decision MQ-1).

    Written only by `services.send_claim_link`, `confirm_claim_identity`, `submit_claim`,
    `approve_claim` and `reject_claim`, which audit every step. `version` is the version the
    claim link is bound to (CORE.15): each new link raises it, so older links are refused.
    """

    Status = ClaimStatus
    Channel = ClaimChannel
    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.OneToOneField(Vendor, on_delete=models.CASCADE, related_name="claim")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, editable=False
    )
    version = models.PositiveIntegerField(default=1, editable=False)
    # Where the current link went: the legal email of record, or the PEC staff picked instead.
    # The link stays bound to that address, and the claimant's account uses it.
    channel = models.CharField(
        max_length=8, choices=Channel.choices, default=Channel.EMAIL, editable=False
    )
    failed_attempts = models.PositiveIntegerField(
        default=0, editable=False, help_text="Wrong VAT numbers or TINs typed with this link."
    )
    # SHA-256 of the nonce of the link with which the VAT number or TIN was confirmed: the
    # account step needs it, and a new link clears it.
    confirmed_link = models.CharField(max_length=64, blank=True, editable=False)
    # PROTECT: the account that submitted the claim stays as long as the claim's history.
    claimant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="vendor_claims",
    )
    link_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ClaimStatus.values),
                name="vendors_claim_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(channel__in=ClaimChannel.values),
                name="vendors_claim_channel_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"claim of {self.vendor} ({self.get_status_display().lower()})"


class RequirementPurpose(models.TextChoices):
    PAYOUT = engine.PAYOUT, "Payout request"
    ACTIVATION = engine.ACTIVATION, "Vendor activation"


class RequirementRegion(models.TextChoices):
    EU = engine.EU, "EU member states"
    NON_EU = engine.NON_EU, "Outside the EU"


def legal_field_label(name: str) -> str:
    """The verbose name of a vendor legal field, e.g. "PEC address" for `pec`."""
    return str(getattr(Vendor._meta.get_field(name), "verbose_name", name))


LEGAL_FIELD_CHOICES = [
    (name, legal_field_label(name)[:1].upper() + legal_field_label(name)[1:])
    for name in engine.REQUIREMENT_FIELDS
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
    region = models.CharField(
        max_length=8,
        choices=RequirementRegion.choices,
        blank=True,
        help_text="Empty: every region. EU or non-EU: every vendor established there.",
    )
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
                fields=["country", "region", "legal_form", "field_name", "purpose"],
                name="vendors_requirement_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(field_name__in=engine.REQUIREMENT_FIELDS),
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
            models.CheckConstraint(
                condition=models.Q(region__in=["", *RequirementRegion.values]),
                name="vendors_requirement_region_valid",
            ),
        ]

    def __str__(self) -> str:
        scope = (
            " ".join(
                filter(
                    None,
                    [
                        self.country,
                        self.get_region_display() if self.region else "",
                        self.legal_form,
                    ],
                )
            )
            or "all vendors"
        )
        return f"{self.field_name} for {self.purpose} ({scope})"

    def as_rule(self) -> engine.Requirement:
        return engine.Requirement(
            field=self.field_name,
            country=self.country,
            legal_form=self.legal_form,
            purpose=self.purpose,
            region=self.region,
        )


class VendorContract(BaseModel):
    """A version of the vendor contract (VEN.5); its texts are `VendorContractTranslation` rows.

    Created and edited through `services.save_contract`, which numbers the versions and keeps
    their start dates in order. The version in force is the latest one already started
    (`services.current_contract`). Once a vendor has accepted a version, the version and its
    texts can no longer change or be deleted: new terms are a new version. `save` and `delete`
    raise `engine.ContractLocked`; a database trigger (migration 0011) refuses any other write.

    Writes to a version or its texts lock the version row, like `services.accept_contract`, so
    an edit and an acceptance of the same version run one after the other.
    """

    version = models.PositiveIntegerField(unique=True, editable=False)
    valid_from = models.DateTimeField(help_text="When this version replaces the previous one.")
    note = models.CharField(max_length=200, blank=True, help_text="Internal: what changed.")

    class Meta:
        ordering = ["-version"]

    def __str__(self) -> str:
        return f"Vendor contract v{self.version}"

    def translation(self, language: str) -> "Resolved[VendorContractTranslation]":
        """The published text in `language`, or the English one with a fallback flag."""
        return resolve_translation(self.translations.all(), language)

    @property
    def is_accepted(self) -> bool:
        return self.pk is not None and self.acceptances.exists()

    def save(self, *args: Any, **kwargs: Any) -> None:
        with transaction.atomic():
            if not self._state.adding:
                _check_not_accepted(self.pk, f"{self} has been accepted; publish a new version")
            super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        with transaction.atomic():
            _check_not_accepted(self.pk, f"{self} has been accepted; it cannot be deleted")
            return super().delete(*args, **kwargs)


def _check_not_accepted(contract_id: int | None, message: str) -> None:
    """Lock the contract version row, then raise `engine.ContractLocked` if it was accepted.

    Call inside a transaction: the lock holds until it ends, so an acceptance of the version
    (`services.accept_contract` takes the same lock) waits for the write, or the write for it.
    """
    if contract_id is None:
        return
    list(VendorContract.objects.select_for_update().filter(pk=contract_id).values_list("pk"))
    if ContractAcceptance.objects.filter(contract_id=contract_id).exists():
        raise engine.ContractLocked(message)


class VendorContractTranslation(TranslationModel):
    """The contract text of one version in one language."""

    translation_of = "contract"
    translated_fields = ("title", "body")

    contract = models.ForeignKey(
        VendorContract, on_delete=models.CASCADE, related_name="translations"
    )
    title = models.CharField(max_length=200)
    body = models.TextField()

    class Meta(TranslationModel.Meta):
        ordering = ["contract", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["contract", "language"], name="vendors_contract_text_language_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contract} [{self.language}]"

    def text_hash(self) -> str:
        """The hash an acceptance of this text records (`ContractAcceptance.text_hash`)."""
        return source_hash(self.source_values())

    def save(self, *args: Any, **kwargs: Any) -> None:
        with transaction.atomic():
            ids = {self.contract_id}
            if not self._state.adding:  # moving a text away from its version changes it too
                stored = type(self)._default_manager.filter(pk=self.pk)
                ids.update(stored.values_list("contract_id", flat=True))
            for contract_id in sorted(i for i in ids if i is not None):
                _check_not_accepted(
                    contract_id, f"{self.contract} has been accepted; its texts are final"
                )
            super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        with transaction.atomic():
            _check_not_accepted(
                self.contract_id, f"{self.contract} has been accepted; its texts are final"
            )
            return super().delete(*args, **kwargs)


class ContractAcceptance(BaseModel):
    """An owner accepted a contract version for a vendor (VEN.5). Append-only (DB trigger).

    Records who, when, from which IP and which text: the language shown and the hash of its
    title and body (`apps.core.translations.source_hash`), and in which onboarding round of the
    vendor (VEN.21). Written by `services.accept_contract`.
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, related_name="contract_acceptances"
    )
    contract = models.ForeignKey(
        VendorContract, on_delete=models.PROTECT, related_name="acceptances"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="contract_acceptances"
    )
    language = LanguageField()
    text_hash = models.CharField(max_length=64, help_text="SHA-256 of the accepted text.")
    accepted_at = models.DateTimeField()
    ip = models.GenericIPAddressField("IP address", null=True, blank=True)
    # The vendor's `onboarding_round` when accepted: after a reopen (VEN.21) the same version is
    # accepted again, in the new round.
    onboarding_round = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["-accepted_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "contract", "onboarding_round"],
                name="vendors_acceptance_vendor_contract_round_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} accepted {self.contract}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("ContractAcceptance is append-only")
        super().save(*args, **kwargs)


class SelfCertification(BaseModel):
    """An owner certified, for a vendor, the DSA article 30(1)(e) statement (CMP.25).

    The vendor offers only products and services that comply with Union law. Append-only (DB
    trigger, migration 0022), like `ContractAcceptance`: stores the text version, the language
    shown and the hash of that text (`engine.self_certification_hash`), and the onboarding round
    of the vendor (VEN.21). Written by `services.certify_platform_duties`.
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="self_certifications")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="self_certifications"
    )
    version = models.PositiveIntegerField()
    language = LanguageField()
    text_hash = models.CharField(max_length=64, help_text="SHA-256 of the certified text.")
    accepted_at = models.DateTimeField()
    onboarding_round = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["-accepted_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "version", "onboarding_round"],
                name="vendors_selfcertification_vendor_version_round_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} self-certified (v{self.version})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("SelfCertification is append-only")
        super().save(*args, **kwargs)


class VendorFeatureFlag(BaseModel):
    """One vendor's override of a feature flag (ARC.8, `apps.core.flags`).

    On or off for this vendor whatever the platform value, except that a kill switch pulled for
    the platform stays off for every vendor. No row: the vendor follows the platform. Written by
    `services.save_vendor_flag` and `delete_vendor_flag` (audited); read with
    `apps.core.services.flag_enabled(name, vendor=vendor)`, which reaches these rows through the
    `vendor_overrides` related name.
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="feature_flags")
    flag = models.ForeignKey(FeatureFlag, on_delete=models.PROTECT, related_name=VENDOR_OVERRIDES)
    enabled = models.BooleanField(
        default=False, help_text="Unticked: the feature is off for this vendor."
    )
    note = models.CharField(
        max_length=200, blank=True, help_text="Why it was switched, e.g. the incident reference."
    )

    class Meta:
        ordering = ["vendor", "flag"]
        constraints = [
            models.UniqueConstraint(fields=["vendor", "flag"], name="vendors_featureflag_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.flag} {'on' if self.enabled else 'off'} for {self.vendor}"

    def clean(self) -> None:
        if self.flag_id is None:
            return
        spec = self.flag.spec
        if spec is None:
            raise ValidationError({"flag": f"{self.flag.name!r} is not a registered feature flag."})
        if spec.kill_switch and self.enabled:
            raise ValidationError(
                {
                    "enabled": f"{spec.name!r} is a kill switch: an override can only turn it off "
                    "for this vendor, never on (when the platform value is off, it stays off)."
                }
            )


class CrossVendorConsent(BaseModel):
    """A vendor's opt-in to other vendors' suggestions and bundles (spec 04 §4.2, CAT.14).

    Versioned and append-only (database trigger, migration 0013): a change is a new row from
    `valid_from`, written and audited by `services.set_cross_vendor_consent`. The row valid at an
    instant is the latest one started by then; no row means opted out (owner decision Q-32).
    """

    vendor_scope: ClassVar[str] = "vendor"
    objects = VendorScopedManager()

    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, related_name="cross_vendor_consents"
    )
    in_suggestions = models.BooleanField(
        "in other vendors' suggestions",
        default=False,
        help_text="Its products may be suggested (cross-sell, up-sell) by other vendors.",
    )
    in_bundles = models.BooleanField(
        "in other parties' bundles",
        default=False,
        help_text="Its products may be part of bundles created by others (CAT.16).",
    )
    valid_from = models.DateTimeField()
    set_by = models.CharField(max_length=150, help_text="Who recorded this version.")

    class Meta:
        ordering = ["vendor", "-valid_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "valid_from"], name="vendors_crossvendorconsent_unique_start"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} consent from {self.valid_from:%Y-%m-%d %H:%M}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("CrossVendorConsent is append-only")
        super().save(*args, **kwargs)


class VendorApplication(BaseModel):
    """The pending marker of a public "Sell with us" application (VEN.22).

    Written only by `services.apply_to_sell`, and deleted when the applicant confirms the email
    (`confirm_application`) or by the retention job (`purge_unconfirmed_applications`). Its
    existence is what makes an inactive account eligible for the emailed confirmation link: a
    staff-blocked, migrated or staff-created applicant owner has none, so no form submission can
    reactivate it. `version` is the version the link is bound to (CORE.15): every new link
    raises it, so older links are refused. It has no foreign key to the vendor: the vendor is
    the one the account owns, so this is not vendor-owned data.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vendor_application",
        editable=False,
    )
    version = models.PositiveIntegerField(default=1, editable=False)
    link_sent_at = models.DateTimeField(
        null=True, blank=True, editable=False, help_text="When the current link was queued."
    )
    reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="When the one reminder was queued; never more than one (D-24).",
    )

    def __str__(self) -> str:
        return f"application of {self.user_id} (link v{self.version})"


TWO_PLACES = Decimal("0.01")


class VendorPlan(BaseModel):
    """A plan: a bundle of limits and features, data not code (spec 04, VEN.2).

    At launch a plan is only a commission scope (`CommissionRule.plan`) and limits (D-04: no
    paid-plan fee is charged before VEN.19). `features` is a JSON object of flags such as
    `featured` or `api_sync`. Changes go through `services.save_plan` (audited).

    Which plan a vendor is on is not stored here: the assignment, with effective dates, belongs
    to VEN.19. Until then `services.commission_rule_for` takes the plan as an argument, and the
    caller must pass the plan in force at the time of the order (never today's plan for an old
    order).
    """

    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=80)
    max_products = models.PositiveIntegerField(null=True, blank=True, help_text="Empty: unlimited.")
    features = models.JSONField(default=dict, blank=True, help_text="Feature flags (an object).")
    monthly_fee = MoneyField(
        null=True, blank=True, help_text="Not charged before VEN.19 (D-04); empty: no fee."
    )

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(monthly_fee__isnull=True) | models.Q(monthly_fee__gte=0),
                name="vendors_plan_fee_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if not isinstance(self.features, dict):
            raise ValidationError({"features": "Features must be a JSON object."})


class CommissionRule(Versioned):
    """A versioned commission rule (VEN.2): never edited, a change is a new row valid from a date.

    Scope: one vendor, one plan, or neither (the platform default); `engine.select_rule` picks
    vendor, then plan, then default. The database refuses overlapping periods within a scope, an
    implicit 0% (a zero percent needs a `promo_label`), out-of-range rates, and, with a trigger
    (migration 0027), any edit or delete: the only write an existing row takes is closing
    `valid_to` once. Orders freeze `snapshot()`, never a live reference.
    """

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="commission_rules",
        help_text="Empty: not specific to one vendor.",
    )
    plan = models.ForeignKey(
        VendorPlan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="commission_rules",
        help_text="Empty: not specific to one plan.",
    )
    version = models.PositiveIntegerField(
        editable=False, help_text="Counts up within the scope, from 1."
    )
    percent = models.DecimalField(max_digits=5, decimal_places=2, help_text="E.g. 20.00.")
    base = models.CharField(max_length=24, choices=[(b, b) for b in engine.COMMISSION_BASES])
    fee_percent = models.DecimalField(
        "payment fee percent", max_digits=5, decimal_places=2, help_text="E.g. 4.00."
    )
    fee_fixed = MoneyField("payment fee fixed", help_text="E.g. 2.00.")
    fee_base = models.CharField(
        max_length=16,
        choices=[(b, b) for b in engine.FEE_BASES],
        default=engine.GROSS_PAID,
    )
    promo_label = models.CharField(max_length=80, blank=True, help_text="E.g. launch promo.")

    class Meta:
        ordering = ["vendor_id", "plan_id", "-valid_from"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(percent__gte=0, percent__lte=100),
                name="vendors_commissionrule_percent_range",
            ),
            models.CheckConstraint(
                condition=models.Q(percent__gt=0) | ~models.Q(promo_label=""),
                name="vendors_commissionrule_no_implicit_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(fee_percent__gte=0, fee_percent__lte=100),
                name="vendors_commissionrule_fee_percent_range",
            ),
            models.CheckConstraint(
                condition=models.Q(fee_fixed__gte=0),
                name="vendors_commissionrule_fee_fixed_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(base__in=engine.COMMISSION_BASES),
                name="vendors_commissionrule_base_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(fee_base__in=engine.FEE_BASES),
                name="vendors_commissionrule_fee_base_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(vendor__isnull=True) | models.Q(plan__isnull=True),
                name="vendors_commissionrule_one_scope",
            ),
            valid_period_check("vendors_commissionrule_valid_to_after_valid_from"),
            models.UniqueConstraint(
                Coalesce("vendor", models.Value(0)),
                Coalesce("plan", models.Value(0)),
                "version",
                name="vendors_commissionrule_unique_version",
            ),
            ExclusionConstraint(
                name="vendors_commissionrule_no_overlap",
                expressions=[
                    (Coalesce("vendor", models.Value(0)), RangeOperators.EQUAL),
                    (Coalesce("plan", models.Value(0)), RangeOperators.EQUAL),
                    (TsTzRange("valid_from", "valid_to"), RangeOperators.OVERLAPS),
                ],
                violation_error_message="Commission rules of one scope must not overlap.",
            ),
        ]

    def __str__(self) -> str:
        who = self.vendor or self.plan or "default"
        return f"{who} v{self.version}: {self.percent}% from {self.valid_from:%Y-%m-%d}"

    def clean(self) -> None:
        # A field that failed its own validation is None here: its error is already reported.
        if None in (self.percent, self.fee_percent, self.fee_fixed):
            return
        errors = engine.commission_rule_errors(
            percent=self.percent,
            base=self.base,
            fee_percent=self.fee_percent,
            fee_fixed=self.fee_fixed,
            fee_base=self.fee_base,
            promo_label=self.promo_label,
        )
        if self.vendor_id is not None and self.plan_id is not None:
            errors["plan"] = "A rule is for a vendor or for a plan, not both."
        if errors:
            raise ValidationError(errors)

    def snapshot(self) -> dict[str, Any]:
        """What an order freezes (ORD.2): JSON-safe, with the rule id and version, and without
        the closing date, which can still change after the order."""
        return {
            "rule_id": str(self.public_id),
            "version": self.version,
            "percent": str(self.percent.quantize(TWO_PLACES, ROUND_HALF_UP)),
            "base": self.base,
            "fee_percent": str(self.fee_percent.quantize(TWO_PLACES, ROUND_HALF_UP)),
            "fee_fixed": str(self.fee_fixed.quantize(TWO_PLACES, ROUND_HALF_UP)),
            "fee_base": self.fee_base,
            "promo_label": self.promo_label,
            "valid_from": self.valid_from.isoformat(),
        }
