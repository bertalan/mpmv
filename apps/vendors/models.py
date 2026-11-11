"""Vendors, their legal identity and their per-language storefront texts and policies (spec 04
§4.2, VEN.1, VEN.12).

Fields owned by later tasks are added there: `plan` (VEN.2), `payout_account` (VEN.6),
`dac7_complete` (CMP.1), `logo` (media/images). Storefront texts follow the shared translation
workflow (`apps.core.models.TranslationModel`, I18N.2). Which legal fields are required, and for
what, is data: `LegalFieldRequirement` (VEN.12). The versioned vendor contract and its
append-only acceptances are VEN.5.
"""

import dataclasses
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
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
    resolve_translation,
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


class Vendor(BaseModel):
    Status = VendorStatus
    SalesMode = VendorSalesMode
    LegalForm = LegalForm
    FiscalRegime = FiscalRegime
    ViesStatus = ViesStatus

    # Tenant isolation (CORE.3): a vendor is its own tenant.
    vendor_scope: ClassVar[str] = "pk"
    objects = VendorScopedManager()

    legal_name = models.CharField(
        max_length=200,
        help_text=_(
            "The full legal name of your business as in the company register, or your own "
            "first and last name if you are a sole trader. It appears on invoices and "
            "contracts. Required: the profile cannot be saved without it."
        ),
    )
    display_name = models.CharField(
        max_length=150,
        help_text=_(
            "The shop name your buyers see, for example 'Rossi Ceramics'. Required: the "
            "shop cannot be shown without a name."
        ),
    )
    slug = models.SlugField(
        max_length=engine.SLUG_MAX_LENGTH,
        unique=True,
        blank=True,
        help_text=_(
            "The short name in your shop's web address, for example 'rossi-ceramics'. Leave"
            " it empty and we make it from the shop name. Changing it later breaks the "
            "links people already have."
        ),
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
    country = CountryField(
        help_text=_(
            "The country where your business is established, as in the company register. It"
            " decides which legal details we ask you for. Required: the profile cannot be "
            "saved without it."
        )
    )
    contact_email = models.EmailField(
        help_text=_(
            "The email address where the platform writes to you about your shop. Required: "
            "without it we cannot reach you."
        )
    )
    phone = models.CharField(
        max_length=32,
        blank=True,
        help_text=_(
            "A phone number where we can reach you, with the country prefix, for example "
            "+39 06 1234 5678. Optional, up to 32 characters."
        ),
    )
    sales_mode_default = models.CharField(
        max_length=16,
        choices=SalesMode.choices,
        default=SalesMode.BUY,
        help_text=_(
            "How your products are sold unless a product says otherwise: buyers order and "
            "pay online, or they first send you an enquiry."
        ),
    )
    enquiries_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Tick it if you want to accept enquiries from buyers about your products. "
            "Unticked: you do not take enquiries."
        ),
    )
    legacy_wp_user_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        unique=True,
        help_text=_(
            "Staff only: the vendor's user number in the old system, kept to link migrated "
            "data. Leave it as it is."
        ),
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
    legal_form = models.CharField(
        max_length=24,
        choices=LegalForm.choices,
        blank=True,
        help_text=_(
            "The legal form of your business, as in the company register. Not sure which "
            "one you are? Ask your accountant."
        ),
    )
    first_name = models.CharField(
        max_length=60,
        blank=True,
        help_text=_(
            "Sole traders only: your first name, as on your identity document. It goes on "
            "the invoices we issue to you."
        ),
    )
    last_name = models.CharField(
        max_length=60,
        blank=True,
        help_text=_(
            "Sole traders only: your last name, as on your identity document. It goes on "
            "the invoices we issue to you."
        ),
    )
    # DAC7 (VEN.23), sole proprietors: personal data, encrypted at rest and masked in the audit
    # log (SEC.3).
    date_of_birth = EncryptedDateField(
        null=True,
        blank=True,
        help_text=_(
            "Sole traders only: your date of birth. It is asked for the platform's yearly "
            "DAC7 reporting duty, and it is stored encrypted."
        ),
    )
    place_of_birth = EncryptedCharField(
        max_length=100,
        blank=True,
        help_text=_(
            "Sole traders only: if your country issues no tax identification number, the "
            "city and country where you were born. It is asked for the platform's yearly "
            "DAC7 reporting duty and stored encrypted. Not sure? Ask your accountant."
        ),
    )
    vat_number = models.CharField(
        "VAT number",
        max_length=32,
        blank=True,
        help_text=_(
            "Your VAT number, with or without the country prefix, for example "
            "IT12345678901. We check it in the EU VIES register. Not sure if you have one? "
            "Ask your accountant."
        ),
    )
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
        help_text=_(
            "Italy: your codice fiscale, 16 characters for a person or 11 digits for a "
            "company. Elsewhere: your national tax identification number (TIN). It is "
            "stored encrypted."
        ),
    )
    tin_country = CountryField(
        "TIN issuing country",
        blank=True,
        help_text=_(
            "The country that issued your tax identification number. We check the format "
            "for that country. It is asked for the platform's yearly DAC7 reporting duty."
        ),
    )
    tin_same_as_vat = models.BooleanField(
        "TIN same as the VAT number",
        default=False,
        help_text=(
            _(
                "Companies only: tick it if your tax identification number is the same as your "
                "VAT number. In the countries where this is allowed we fill it in once VIES "
                "confirms the VAT number. Not sure? Ask your accountant."
            )
        ),
    )
    tax_residence_country = CountryField(
        "country of tax residence",
        blank=True,
        help_text=_(
            "The country where you are resident for tax. It is asked for the platform's "
            "yearly DAC7 reporting duty. Not sure? Ask your accountant."
        ),
    )
    fiscal_regime = models.CharField(
        max_length=4,
        choices=FiscalRegime.choices,
        blank=True,
        help_text=_(
            "The tax regime that applies to your business, chosen from the official "
            "FatturaPA list (RF19 is the flat-rate regime, forfettario). Not sure which one"
            " applies? Ask your accountant: we cannot choose it for you."
        ),
    )
    cross_border_franchise_number = models.CharField(
        "cross-border franchise number",
        max_length=20,
        blank=True,
        help_text=(
            _(
                "Only for regime RF20: the number ending in EX that your country of "
                "establishment issued for the cross-border VAT franchise, for example "
                "DE123456789EX."
            )
        ),
    )
    ateco_code = models.CharField(
        "ATECO code",
        max_length=8,
        blank=True,
        help_text=_(
            "Italy only: the ATECO code of your business activity, for example 47.91.10. It"
            " is on your VAT registration; if you do not know it, ask your accountant."
        ),
    )
    eori = models.CharField(
        "EORI number",
        max_length=17,
        blank=True,
        help_text=_(
            "Your EORI number, the customs identification number for businesses that move "
            "goods in or out of the EU. Leave it empty if you do not have one."
        ),
    )
    business_registration_number = models.CharField(
        "business registration number",
        max_length=40,
        blank=True,
        help_text=_(
            "Companies only: the number from the company register (in Italy, the REA "
            "number), if you have one. It is asked for the platform's yearly DAC7 reporting"
            " duty."
        ),
    )
    registered_street = models.CharField(
        "registered office street",
        max_length=200,
        blank=True,
        help_text=_(
            "The street of your registered office (sede legale), as in the company "
            "register, without the number."
        ),
    )
    registered_street_number = models.CharField(
        "registered office street number",
        max_length=16,
        blank=True,
        help_text=_("The street number of your registered office, for example 12/B."),
    )
    registered_postal_code = models.CharField(
        "registered office postal code",
        max_length=16,
        blank=True,
        help_text=_("The postal code of your registered office, for example 00184."),
    )
    registered_city = models.CharField(
        "registered office city",
        max_length=100,
        blank=True,
        help_text=_("The city of your registered office."),
    )
    registered_province = models.CharField(
        "registered office province",
        max_length=64,
        blank=True,
        help_text=_(
            "Italy: the 2-letter province code, for example RM. Elsewhere: the state or region."
        ),
    )
    registered_country = CountryField(
        "registered office country",
        blank=True,
        help_text=_("The country of your registered office."),
    )
    rea_office = models.CharField(
        "REA office",
        max_length=2,
        choices=PROVINCE_CHOICES,
        blank=True,
        help_text=_(
            "Italy only: the province of the company register office (REA) where your "
            "company is registered, for example RM."
        ),
    )
    rea_number = models.CharField(
        "REA number",
        max_length=20,
        blank=True,
        help_text=_("Italy only: your REA number from the company register, for example 123456."),
    )
    share_capital = MoneyField(
        null=True,
        blank=True,
        help_text=_(
            "Companies only: the share capital as in the company register, for example 10000.00."
        ),
    )
    sole_shareholder = models.CharField(
        max_length=2,
        choices=SoleShareholder.choices,
        blank=True,
        help_text=_(
            "Companies only: SU if one person owns all the shares, SM if there are several "
            "shareholders. Not sure? Ask your accountant."
        ),
    )
    liquidation_status = models.CharField(
        max_length=2,
        choices=LiquidationStatus.choices,
        blank=True,
        help_text=_(
            "Companies only: LS if your company is in liquidation, LN if it is not, as in "
            "the company register."
        ),
    )
    sdi_recipient_code = models.CharField(
        "SDI recipient code",
        max_length=7,
        blank=True,
        help_text=(
            _(
                "The 7-character code of the SDI channel (the Italian tax system) that receives"
                " e-invoices addressed to you. 0000000 is used together with a PEC address, "
                "which you give in the PEC field; XXXXXXX is for businesses established outside"
                " Italy. Your accountant or invoicing software can tell you the code."
            )
        ),
    )
    pec = models.EmailField(
        "PEC address",
        blank=True,
        help_text=_(
            "Your certified email (PEC) address, if you have one. It is required when the "
            "SDI recipient code is 0000000."
        ),
    )
    fiscal_representative_name = models.CharField(
        max_length=80,
        blank=True,
        help_text=_(
            "Only if you are established outside the EU and have appointed a fiscal "
            "representative in Italy: their name, as in the appointment. Otherwise leave it"
            " empty."
        ),
    )
    fiscal_representative_vat_number = models.CharField(
        "fiscal representative VAT number",
        max_length=16,
        blank=True,
        help_text=_(
            "The Italian VAT number of your fiscal representative (11 digits). Leave it "
            "empty if you have none."
        ),
    )
    establishment_street = models.CharField(
        "permanent establishment street",
        max_length=200,
        blank=True,
        help_text=_(
            "Only if you have a permanent establishment (a branch or fixed place of "
            "business) apart from your registered office: its street, without the number. "
            "Otherwise leave the whole address empty; if unsure, ask your accountant."
        ),
    )
    establishment_street_number = models.CharField(
        "permanent establishment street number",
        max_length=16,
        blank=True,
        help_text=_("The street number of your permanent establishment, for example 12/B."),
    )
    establishment_postal_code = models.CharField(
        "permanent establishment postal code",
        max_length=16,
        blank=True,
        help_text=_("The postal code of your permanent establishment, for example 00184."),
    )
    establishment_city = models.CharField(
        "permanent establishment city",
        max_length=100,
        blank=True,
        help_text=_("The city of your permanent establishment."),
    )
    establishment_province = models.CharField(
        "permanent establishment province",
        max_length=64,
        blank=True,
        help_text=_(
            "Italy: the 2-letter province code, for example RM. Elsewhere: the state or region."
        ),
    )
    establishment_country = CountryField(
        "permanent establishment country",
        blank=True,
        help_text=_("The country of your permanent establishment."),
    )
    # SEC.3: encrypted at rest and masked in the audit log.
    iban = EncryptedCharField(
        "IBAN",
        max_length=34,
        blank=True,
        help_text=_(
            "The IBAN of the bank account that receives your payouts. Check it carefully: a"
            " wrong IBAN can delay your payment. It is stored encrypted."
        ),
    )
    bic = models.CharField(
        "BIC",
        max_length=11,
        blank=True,
        help_text=_("The BIC (SWIFT) code of your bank, 8 or 11 characters, if you have it."),
    )
    bank_name = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("The name of your bank. Optional: it helps us recognise the account."),
    )
    account_holder_name = models.CharField(
        "account holder name",
        max_length=140,
        blank=True,
        help_text=_(
            "Only if the bank account is not in the name of your business: the name of the "
            "account holder. It is asked for the platform's yearly DAC7 reporting duty."
        ),
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
    tagline = models.CharField(
        max_length=200,
        blank=True,
        help_text=_(
            "A short line under your shop name, up to 200 characters, for example 'Handmade"
            " ceramics from Umbria'. Leave it empty to show none."
        ),
    )
    description = RichTextField(
        blank=True,
        help_text=_(
            "Tell buyers about your shop: who you are and what you make or sell. Leave it "
            "empty and your shop page has no description."
        ),
    )
    shipping_policy = RichTextField(
        blank=True,
        help_text=_(
            "Tell buyers how and when you ship, and what it costs. Buyers read it in your "
            "shop before they order."
        ),
    )
    refund_policy = RichTextField(
        blank=True,
        help_text=_(
            "Tell buyers how to ask for a refund, how long it takes and who pays the return"
            " shipping. Buyers read it in your shop before they order."
        ),
    )
    cancellation_policy = RichTextField(
        blank=True,
        help_text=_(
            "Tell buyers until when they can cancel an order and how, for example 'before "
            "the parcel is handed to the courier'. Buyers read it in your shop before they "
            "order."
        ),
    )

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

    country = CountryField(
        blank=True,
        help_text=_(
            "The rule applies to vendors established in this country. Leave it empty for "
            "every country."
        ),
    )
    region = models.CharField(
        max_length=8,
        choices=RequirementRegion.choices,
        blank=True,
        help_text=_(
            "Leave it empty for every region, or choose EU or non-EU to apply the rule to "
            "every vendor established there."
        ),
    )
    legal_form = models.CharField(
        max_length=24,
        choices=LegalForm.choices,
        blank=True,
        help_text=_(
            "The rule applies to vendors with this legal form. Leave it empty for every legal form."
        ),
    )
    field_name = models.CharField(
        "field",
        max_length=64,
        choices=LEGAL_FIELD_CHOICES,
        help_text=_(
            "The vendor profile field that must be filled in. Required: a rule needs a field."
        ),
    )
    purpose = models.CharField(
        max_length=16,
        choices=Purpose.choices,
        default=Purpose.PAYOUT,
        help_text=_(
            "What the field is needed for: a payout request or the activation of the vendor."
        ),
    )
    active = models.BooleanField(
        default=True, help_text=_("Untick it to stop applying the rule without deleting it.")
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Why the field is required, for the record. Up to 200 characters."),
    )

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
    valid_from = models.DateTimeField(
        help_text=_(
            "When this version replaces the previous one. Required. Once a vendor has "
            "accepted the version it can no longer be changed."
        )
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        help_text=_(
            "Internal note on what changed in this version. Vendors do not see it. Up to "
            "200 characters."
        ),
    )

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
    body = models.TextField(
        help_text=_(
            "The full text of the contract in this language. Required. Once a vendor has "
            "accepted the version it is final: new terms need a new version."
        )
    )

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
        Vendor,
        on_delete=models.PROTECT,
        related_name="contract_acceptances",
        help_text=_("The vendor that accepted the contract."),
    )
    contract = models.ForeignKey(
        VendorContract,
        on_delete=models.PROTECT,
        related_name="acceptances",
        help_text=_("The contract version that was accepted."),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="contract_acceptances",
        help_text=_("The person who accepted the contract on behalf of the vendor."),
    )
    language = LanguageField(
        help_text=_("The language of the text that was shown when the contract was accepted.")
    )
    text_hash = models.CharField(
        max_length=64,
        help_text=_(
            "A fingerprint (SHA-256) of the exact text that was accepted, to prove later "
            "that it has not changed."
        ),
    )
    accepted_at = models.DateTimeField(help_text=_("When the contract was accepted."))
    ip = models.GenericIPAddressField(
        "IP address",
        null=True,
        blank=True,
        help_text=_(
            "The IP address the acceptance came from, kept as evidence. Empty if it was not known."
        ),
    )
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

    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="feature_flags",
        help_text=_("The vendor this setting applies to."),
    )
    flag = models.ForeignKey(
        FeatureFlag,
        on_delete=models.PROTECT,
        related_name=VENDOR_OVERRIDES,
        help_text=_("The feature to switch on or off for this vendor."),
    )
    enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Ticked: the feature is on for this vendor. Unticked: it is off, whatever the "
            "platform setting is. An emergency switch can only be turned off here."
        ),
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        help_text=_(
            "Why it was switched, for example the incident or request reference. Up to 200 "
            "characters."
        ),
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
        Vendor,
        on_delete=models.PROTECT,
        related_name="cross_vendor_consents",
        help_text=_("The vendor this consent belongs to."),
    )
    in_suggestions = models.BooleanField(
        "in other vendors' suggestions",
        default=False,
        help_text=_(
            "Tick it to let other vendors suggest your products next to theirs. Unticked: "
            "your products are not suggested by others."
        ),
    )
    in_bundles = models.BooleanField(
        "in other parties' bundles",
        default=False,
        help_text=_(
            "Tick it to let others include your products in the bundles they create. "
            "Unticked: they cannot."
        ),
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
