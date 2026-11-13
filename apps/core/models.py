"""Core models: the abstract base, translation and versioned-configuration models, the
append-only audit log, the platform operator, the feature flags, the shared image model and its
licence allow list, the transactional email outbox."""

import json
import posixpath
import uuid
from typing import Any, ClassVar, Self
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import ArrayField, DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import URLValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from wagtail.images.models import AbstractImage, AbstractRendition, Image, get_upload_to
from wagtail.models import Collection

from . import flags, session_policy, translations, uploads
from .countries import OFFICIAL_LANGUAGES, default_allowed_vendor_countries
from .fields import (
    CountryField,
    EncryptedTextField,
    LanguageField,
    SanitisedImageField,
    image_limits,
    read_upload,
)
from .image_licences import (
    Credit,
    LicenceTerms,
    credit_for,
    metadata_errors,
    restricted_licence_error,
)
from .profile_links import profile_key
from .svg import LOGO_COLLECTION_NAME, MAX_SVG_BYTES, UnsafeSvgError, sanitise_svg

# The dedicated platform owner permission (spec 02 roles, CMP.13): never implied by staff or
# admin access, superuser status included. Granted as `core.platform_owner`, directly or through a
# group, and checked by `services.is_platform_owner`, not `has_perm` (true for any superuser).
PLATFORM_OWNER_CODENAME = "platform_owner"
# D-32 (CORE.25): the roles that may open a passkey window ("Do it once" too). The codename is a
# stored identifier (roles hold it), so it keeps its name.
ELEVATE_CODENAME = "elevate"


class BaseModel(models.Model):
    """Base for domain models: bigint PK (default), UUID `public_id` for APIs, timestamps."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TranslationStatus(models.TextChoices):
    MACHINE = translations.MACHINE, "Machine draft (needs review)"
    REVIEWED = translations.REVIEWED, "Reviewed"
    PUBLISHED = translations.PUBLISHED, "Published"


class TranslationModel(BaseModel):
    """Base for every explicit translation table (`*Translation`, spec 09 §9.2, I18N.2).

    One row per translated entity and language holds that language's texts; the row in the
    source language (settings.LANGUAGE_CODE, EN) is the source of the others.

    Reuse contract (checked by `apps.core.checks`, so catalog tables follow it too):

    - set `translation_of` to the name of the ForeignKey to the translated entity, and
      `translated_fields` to the names of its CharField/TextField texts;
    - declare `class Meta(TranslationModel.Meta)`, keep `*TranslationModel.Meta.constraints` and
      add a `UniqueConstraint` on (`translation_of`, "language");
    - read texts for visitors with `resolve_translation(entity.<related_name>.all(), language)`,
      never by filtering on `language` alone: it applies the published-only rule and the
      explicit EN fallback.

    Workflow: rows start as `machine` drafts. Saving the source row stores the hash of its texts
    in `source_hash`; when that hash changes, the reviewed and published rows in other languages
    go back to `machine` ("needs review") and keep the hash they were made from, so a reviewer
    can tell they are stale. Other rows record the source hash when they are created and when
    they leave `machine` (a review is against the current source). Queryset `update()` bypasses
    all this: change texts through `save()`.
    """

    Status = TranslationStatus

    translation_of: ClassVar[str] = ""
    translated_fields: ClassVar[tuple[str, ...]] = ()

    language = LanguageField()
    status = models.CharField(
        max_length=16, choices=TranslationStatus.choices, default=TranslationStatus.MACHINE
    )
    source_hash = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        help_text="Hash of the source-language texts this row was made or reviewed from.",
    )

    class Meta:
        abstract = True
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(status__in=translations.STATUSES),
                name="%(app_label)s_%(class)s_status_valid",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        with transaction.atomic():
            stored = type(self)._default_manager.filter(pk=self.pk)
            previous = stored.values("status", "source_hash").first() if self.pk else None
            source_changed = False
            if self.is_source:
                self.source_hash = translations.source_hash(self.source_values())
                source_changed = previous is None or previous["source_hash"] != self.source_hash
            elif (previous is None and not self.source_hash) or (
                previous is not None
                and previous["status"] == TranslationStatus.MACHINE
                and self.status != TranslationStatus.MACHINE
            ):
                self.source_hash = self.current_source_hash() or ""
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "source_hash"}
            super().save(*args, **kwargs)
            if source_changed:
                self.mark_stale_translations()

    @property
    def is_source(self) -> bool:
        return bool(self.language == translations.source_language())

    def source_values(self) -> dict[str, str]:
        """The translated texts of this row, by field name."""
        return {name: getattr(self, name) for name in self.translated_fields}

    def family(self) -> "models.QuerySet[Self]":
        """Every row (all languages, this one included) of the same translated entity."""
        parent = self._meta.get_field(self.translation_of)
        if not isinstance(parent, models.ForeignKey):
            raise TypeError(f"{type(self).__name__}.translation_of must name a ForeignKey")
        attname = parent.attname
        return type(self)._default_manager.filter(**{attname: getattr(self, attname)})

    def current_source_hash(self) -> str | None:
        """The hash of the source texts as they are now; None when there is no source row."""
        if self.is_source:
            return translations.source_hash(self.source_values())
        source = translations.source_language()
        stored: str | None = (
            self.family().filter(language=source).values_list("source_hash", flat=True).first()
        )
        return stored

    @property
    def is_stale(self) -> bool:
        """True when the source texts changed after this translation was made or reviewed."""
        if self.is_source:
            return False
        current = self.current_source_hash()
        return current is not None and current != self.source_hash

    def mark_stale_translations(self) -> int:
        """Send the reviewed and published rows made from another source text back to review.

        Called by `save()` on the source row when its texts change; returns the rows changed.
        """
        if not self.is_source:
            return 0
        return (
            self.family()
            .exclude(pk=self.pk)
            .exclude(source_hash=self.source_hash)
            .exclude(status=TranslationStatus.MACHINE)
            .update(status=TranslationStatus.MACHINE, updated_at=timezone.now())
        )


def resolve_translation[TM: TranslationModel](
    rows: "models.QuerySet[TM]", language: str
) -> translations.Resolved[TM]:
    """The published row of `rows` in `language`, else the published source-language row.

    `rows` are the translations of one entity, e.g. `vendor.translations.all()`. The result's
    `is_fallback` tells templates to show the "not available in your language" notice.
    """
    candidates = rows.filter(
        status=TranslationStatus.PUBLISHED,
        language__in={language, translations.source_language()},
    )
    return translations.resolve(candidates, language)


class Heartbeat(BaseModel):
    """Proof that the worker and its scheduler are alive: one row per name, updated in place.

    Written by the periodic `core.heartbeat` job (ARC.9); monitoring alerts when `last_beat_at`
    gets old (OPS tasks).
    """

    name = models.CharField(max_length=100, unique=True)
    beats = models.PositiveBigIntegerField(default=0)
    last_beat_at = models.DateTimeField()
    scheduled_for = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.beats} beats, last {self.last_beat_at:%Y-%m-%d %H:%M})"


class Throttle(BaseModel):
    """Failure counter of one throttle key, for progressive lockout and rate limits (CORE.5).

    Kept in PostgreSQL, the only datastore (ADR-0001): every gunicorn worker and container sees
    the same counters, they survive restarts, and `select_for_update` makes each increment
    atomic. A process-local cache would let an attacker multiply the budget by the number of
    workers, and Django's database cache has no atomic increment. Only failures write here,
    so the load is small. Expired rows are purged nightly (`core.purge_throttles`).
    """

    key = models.CharField(max_length=320, unique=True)
    failures = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)

    def __str__(self) -> str:
        return f"{self.key} ({self.failures} failures)"


class AuditLog(models.Model):
    """Who changed what, before/after, and from where. Append-only (DB trigger + save guard).

    The actor is stored by id and label, not as a foreign key: audit rows must outlive users
    and must not cascade on deletion.
    """

    class Source(models.TextChoices):
        ADMIN = "admin"
        VENDOR = "vendor"
        API = "api"
        COMMAND = "command"
        TASK = "task"
        MCP = "mcp"
        SYSTEM = "system"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=150)
    source = models.CharField(max_length=16, choices=Source.choices)
    action = models.CharField(max_length=100, db_index=True)
    target_model = models.CharField(max_length=100)
    target_key = models.CharField(max_length=200)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    # Public id of the vendor the entry belongs to, empty for platform-level entries (SEC.4).
    vendor_key = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        indexes = [models.Index(fields=["target_model", "target_key"])]
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return (
            f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label} {self.action} {self.target_key}"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("AuditLog is append-only")
        super().save(*args, **kwargs)


class TsTzRange(models.Func):
    """PostgreSQL `tstzrange(lower, upper, '[)')`; a NULL upper bound means open-ended."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Versioned(BaseModel):
    """A row of versioned configuration: valid over `[valid_from, valid_to)`, never edited in
    place (spec 01 "History", `apps.core.validity`).

    A change is a new row valid from a date, and the service that adds it closes the current
    open-ended row: `valid_to` (with `updated_at`) is the only thing an existing row may write.
    Declare `valid_period_check(...)` among the subclass's constraints.
    """

    valid_from = models.DateTimeField(
        help_text=_(
            "When this version starts to apply. A change is a new version from a date; the "
            "service that adds it closes the previous one."
        )
    )
    valid_to = models.DateTimeField(null=True, blank=True, editable=False)

    # The only fields an existing row may write: closing its validity period.
    CLOSING_FIELDS: ClassVar[frozenset[str]] = frozenset({"valid_to", "updated_at"})

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        if self.pk is not None and (
            update_fields is None
            or "valid_to" not in update_fields
            or not self.CLOSING_FIELDS.issuperset(update_fields)
        ):
            raise PermissionError(
                f"{type(self).__name__} rows are never edited in place; add a new row instead"
            )
        super().save(*args, **kwargs)


def valid_period_check(name: str) -> models.CheckConstraint:
    """The database rule of a `Versioned` row: an end, when set, comes after the start."""
    return models.CheckConstraint(
        condition=models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=models.F("valid_from")),
        name=name,
    )


def known_language_bases() -> frozenset[str]:
    """Base subtags a contact language may use: the site's languages and the official
    languages of the countries in `OFFICIAL_LANGUAGES`."""
    bases = {code.split("-")[0].lower() for code, _name in settings.LANGUAGES}
    for languages in OFFICIAL_LANGUAGES.values():
        bases.update(languages)
    return frozenset(bases)


def validate_language_code(value: str) -> None:
    """A known language, case-insensitive, optionally with a region (`de`, `NL`, `pt-br`)."""
    base = value.lower().split("-")[0]
    if base not in known_language_bases():
        raise ValidationError(f"{value!r} is not a known language code.")


validate_https_url = URLValidator(schemes=["https"], message="Enter an https:// address.")


MAX_OFFICIAL_PROFILES = 10


def official_profile_errors(urls: Any) -> list[str]:
    """One message per refused entry of a list of official profile links: not a string, not an
    https link, with a user name or password in it, or listed twice."""
    if not isinstance(urls, list):
        return ["Enter a list of links."]
    errors: list[str] = []
    seen: set[str] = set()
    if len(urls) > MAX_OFFICIAL_PROFILES:
        errors.append(f"List at most {MAX_OFFICIAL_PROFILES} profile links.")
    for url in urls:
        text = url.strip() if isinstance(url, str) else ""
        try:
            validate_https_url(text)
        except ValidationError:
            errors.append(f"{text or url!r} is not an https:// link.")
            continue
        try:
            netloc = urlsplit(text).netloc
        except ValueError:
            errors.append(f"{text!r} is not an https:// link.")
            continue
        if "@" in netloc:
            errors.append(f"{text!r} must not contain a user name or password.")
        elif (key := profile_key(text)) is None:
            errors.append(f"{text!r} is not an https:// link.")
        elif key in seen:
            errors.append(f"{text!r} is listed twice.")
        else:
            seen.add(key)
    return errors


class PlatformOperator(Versioned):
    """The legal entity running the marketplace, as versioned configuration (ARC.7).

    Its country, VAT position, e-invoicing channel and DAC7 authority drive tax, invoicing and
    reporting: code reads them with `apps.core.services.current_operator()` and never hard-codes
    a platform country. Rows are history: a change (e.g. a relocation to another EU country) is
    a new row through `configure_platform_operator`, which closes the previous one. Only
    `valid_to` may be written on an existing row, to close it. The countries where vendors may
    be established change only through `set_allowed_vendor_countries` (CMP.13).
    """

    class TaxRegime(models.TextChoices):
        STANDARD = "standard", "Standard VAT regime"
        SMALL_BUSINESS = "small_business", "Small-business / flat-rate scheme (no VAT charged)"

    class CommissionVatMode(models.TextChoices):
        NONE = "none", "No VAT on commissions and fees"
        STANDARD = "standard", "VAT on commissions and fees under the general rules"

    legal_name = models.CharField(
        max_length=200, help_text=_("The legal name of the company that runs the platform.")
    )
    country = CountryField(
        help_text=_(
            "The country where the company is established, as a two-letter code such as 'IT'."
        )
    )
    vat_number = models.CharField(
        "VAT number",
        max_length=32,
        help_text=_(
            "The company's VAT number, starting with the country letters. Ask your "
            "accountant if you are not sure."
        ),
    )
    tax_regime = models.CharField(
        max_length=32,
        choices=TaxRegime.choices,
        help_text=_(
            "How the platform itself is treated for VAT: the standard regime, or the small-"
            "business (flat-rate) scheme with no VAT charged. Ask your accountant if you "
            "are not sure."
        ),
    )
    commission_vat_mode = models.CharField(
        "commission VAT mode",
        max_length=16,
        choices=CommissionVatMode.choices,
        help_text=_(
            "Whether VAT is added to the platform's commissions and fees. Ask your "
            "accountant if you are not sure."
        ),
    )
    dac7_authority_country = CountryField(
        "DAC7 reporting authority country",
        help_text=_(
            "Not used yet: saved for a future feature. The EU country whose tax authority "
            "will get the DAC7 report. Ask your accountant if you are not sure."
        ),
    )
    # CMP.3 (owner decision 2026-11-09): staff alerts, e.g. an overdue GPSR safety case, go to
    # the compliance address, else to the support address.
    support_email = models.EmailField(
        "platform support email",
        blank=True,
        help_text=_(
            "The address users can write to for help. It is published on the public contact page."
        ),
    )
    compliance_email = models.EmailField(
        blank=True,
        help_text=_(
            "Where compliance alerts go, for example an overdue safety case. Leave it empty"
            " to send them to the support address."
        ),
    )
    # VEN.35 (D-40): off by default; switching it on makes every seller without sales terms in
    # force sell on request only. A versioned setting like the rest of this row.
    require_sales_terms = models.BooleanField(
        default=False,
        help_text=_(
            "When on, a seller who has not published sales terms sells on request only. "
            "Sellers who have published terms are not affected. Leave it off until your "
            "sellers have had time to publish their terms."
        ),
    )
    # CMP.5 (DSA articles 11 and 12): the single point of contact for authorities and the means
    # for recipients of the service, published on the public contact page. Recipients reach the
    # platform at `support_email` and, optionally, at a contact form (`user_contact_url`).
    authority_contact_email = models.EmailField(
        "authorities' contact email",
        blank=True,
        help_text=_(
            "The single contact address for authorities. The law (DSA article 11) asks for "
            "one; it is published on the public contact page."
        ),
    )
    authority_contact_languages = ArrayField(
        models.CharField(max_length=8, validators=[validate_language_code]),
        default=list,
        blank=True,
        verbose_name="authorities' contact languages",
        help_text=_(
            "The languages authorities may write in, as codes such as 'en' or 'it', "
            "separated by commas. Give an official language of the country and one that "
            "many people understand."
        ),
    )
    user_contact_url = models.URLField(
        "users' contact form URL",
        validators=[validate_https_url],
        blank=True,
        help_text=_(
            "An optional web form for users, next to the support email. It must start with "
            "https://."
        ),
    )
    user_contact_languages = ArrayField(
        models.CharField(max_length=8, validators=[validate_language_code]),
        default=list,
        blank=True,
        verbose_name="users' contact languages",
        help_text=_(
            "The languages the support team answers in, as codes such as 'en' or 'it', "
            "separated by commas."
        ),
    )
    # SEO.8 follow-up (owner decision): the public face of the operator in its Organization
    # JSON-LD. The logo comes from the platform logos collection with its image licence fields.
    logo = models.ForeignKey(
        "core.MediaImage",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_(
            "The platform's logo, shown to search engines. Pick an image from the "
            "'Logos and icons' collection that has its licence and author filled in."
        ),
    )
    official_profiles = ArrayField(
        models.URLField(max_length=300),
        default=list,
        blank=True,
        help_text=_(
            "Official profiles of the platform, one https:// link each, for search "
            "engines. Example: its Wikidata item, its LinkedIn page. No user names or passwords."
        ),
    )
    # D-21, CMP.13: changed only by a platform owner through
    # `apps.core.services.set_allowed_vendor_countries`; other new versions carry it over.
    # D-32 (CORE.25): the passkey window timers and the three action lists, sparse over the D-32
    # defaults (`core.session_policy`); `{}` is the defaults. Tunable without code (trial).
    session_policy = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Staff session settings that replace the defaults: timers and lists of actions."
            " Leave it empty to keep the defaults. A value that does not fit is refused "
            "when you save."
        ),
    )
    allowed_vendor_countries = ArrayField(
        CountryField(),
        default=default_allowed_vendor_countries,
        editable=False,
        verbose_name="allowed vendor establishment countries",
        help_text="A vendor is activated only when its registered office or permanent "
        "establishment is in one of these countries.",
    )

    class Meta:
        ordering = ["-valid_from"]
        permissions = [
            (
                PLATFORM_OWNER_CODENAME,
                "Platform owner: owner-only settings, such as the allowed vendor countries",
            ),
            (
                ELEVATE_CODENAME,
                "May open a passkey window or do a sensitive action once",
            ),
        ]
        constraints = [
            valid_period_check("core_platformoperator_valid_to_after_valid_from"),
            models.CheckConstraint(
                condition=models.Q(allowed_vendor_countries__len__gt=0),
                name="core_platformoperator_allowed_vendor_countries_not_empty",
            ),
            ExclusionConstraint(
                name="core_platformoperator_no_overlap",
                expressions=[(TsTzRange("valid_from", "valid_to"), RangeOperators.OVERLAPS)],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.legal_name} ({self.country}) from {self.valid_from:%Y-%m-%d}"

    def clean(self) -> None:
        super().clean()
        try:
            session_policy.parse_policy(self.session_policy)
        except ValueError as exc:
            raise ValidationError({"session_policy": str(exc)}) from exc
        problems = official_profile_errors(self.official_profiles)
        if problems:
            raise ValidationError({"official_profiles": problems})
        logo = self.logo
        if logo is not None:
            if svg_collection_refusal(logo.collection) is not None:
                raise ValidationError(
                    {"logo": "Choose an image from the 'Logos and icons' collection."}
                )
            if logo.needs_review:
                raise ValidationError(
                    {"logo": "This image has no licence or author yet: complete them first."}
                )
        # Codes are stored lower-case (`PT-BR` -> `pt-br`), without duplicates.
        for name in ("authority_contact_languages", "user_contact_languages"):
            codes = [code.strip().lower() for code in getattr(self, name) or []]
            setattr(self, name, list(dict.fromkeys(codes)))


# Per-vendor overrides are vendor-owned rows (CORE.3) in apps.vendors, which core cannot import:
# their ForeignKey to FeatureFlag uses this related name, and core reads them through it.
VENDOR_OVERRIDES = "vendor_overrides"


class FeatureFlag(BaseModel):
    """The staff switch of one registered flag (ARC.8, `apps.core.flags`).

    `state` is `default` (this environment's default, `settings.FEATURE_FLAGS`, else the code
    default), `on` or `off`. Per-vendor overrides are vendor-owned rows in the vendors app
    (`VendorFeatureFlag`, related name `vendor_overrides`). Rows are never deleted: set the state
    back to `default` instead. Write through `apps.core.services.save_flag` (audited); read with
    `flag_enabled`.
    """

    class State(models.TextChoices):
        DEFAULT = flags.DEFAULT, "Environment default"
        ON = flags.ON, "On"
        OFF = flags.OFF, "Off"

    name = models.CharField(
        max_length=flags.NAME_MAX_LENGTH,
        unique=True,
        help_text=_("The name of the feature switch, exactly as it is registered in the code."),
    )
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.DEFAULT,
        help_text=_(
            "'Default' follows the setting of this environment, or the default of the code "
            "if there is none. 'On' and 'Off' switch the feature on or off."
        ),
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        help_text=_(
            "Why you changed it, for example an incident reference. It helps the next "
            "person who looks at it."
        ),
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=flags.STATES), name="core_featureflag_state_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if not flags.is_registered(self.name):
            raise ValidationError({"name": f"{self.name!r} is not a registered feature flag."})

    @property
    def spec(self) -> flags.FlagSpec | None:
        """The flag's declaration in code; None for a row left by removed code."""
        return flags.spec_of(self.name) if flags.is_registered(self.name) else None


class ImageLicence(BaseModel):
    """An entry of the image licence allow list (CMS.8, spec 10 §10.2), edited by staff.

    Written through `services.save_image_licence` (audited). Non-commercial and
    no-derivatives licences are refused. Entries are never deleted while an image uses them.
    """

    code = models.CharField(
        max_length=40,
        unique=True,
        help_text=_("A short code that is unique, for example 'CC-BY-4.0'."),
    )
    name = models.CharField(
        max_length=100, help_text=_("The name shown in image credits, for example 'CC BY 4.0'.")
    )
    url = models.URLField(
        "licence URL", blank=True, help_text=_("A link to the text of the licence. Optional.")
    )
    attribution_required = models.BooleanField(
        default=False, help_text=_("Tick it when images under this licence must show a credit.")
    )
    source_required = models.BooleanField(
        default=True,
        help_text=_("Tick it when images under this licence need the web address they come from."),
    )
    permission_required = models.BooleanField(
        default=False,
        help_text=_(
            "Tick it when images under this licence need a reference to a written "
            "permission that is stored."
        ),
    )

    class Meta:
        ordering = ("code",)
        verbose_name = "image licence"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        error = restricted_licence_error(self.code, self.name, self.url)
        if error is not None:
            raise ValidationError({"code": error})

    @property
    def terms(self) -> LicenceTerms:
        return LicenceTerms(
            code=self.code,
            name=self.name,
            url=self.url,
            attribution_required=self.attribution_required,
            source_required=self.source_required,
            permission_required=self.permission_required,
        )


class GeocodeCacheEntry(BaseModel):
    """The candidates of one geocoding lookup, by normalised address and language (EVT.12).

    `key` is the SHA-256 of the normalised address (country, postcode, city, street, case folded,
    whitespace collapsed); `normalised_address` keeps the text it was made from. Only the venue
    address typed in an event form is ever looked up, never a member's address. Written by
    `apps.core.geocoding.services.geocode_address`; expired rows are purged nightly.
    """

    key = models.CharField(max_length=64)
    language = models.CharField(max_length=16)
    normalised_address = models.TextField()
    provider = models.CharField(max_length=32)
    candidates = models.JSONField(default=list)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key", "language"], name="core_geocode_cache_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.normalised_address} [{self.language}] via {self.provider}"


class GeocoderState(BaseModel):
    """Shared state of one geocoding provider (EVT.12): the limiter's next free slot and the
    circuit breaker. In PostgreSQL so that every process sees the same budget (no Redis, spec
    01); `select_for_update` serialises the slot reservations."""

    provider = models.CharField(max_length=32, unique=True)
    next_slot_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)  # consecutive failures or rate limits
    open_until = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.provider} ({self.failures} failures)"


def svg_collection_refusal(collection: Collection | None) -> str | None:
    """None if an SVG may sit in `collection` (an `SvgCollection` or one below it); the reason
    it may not otherwise (THM.12)."""
    if (
        collection is not None
        and Collection.objects.ancestor_of(collection, inclusive=True)
        .filter(svg_policy__isnull=False)
        .exists()
    ):
        return None
    allowed = Collection.objects.filter(svg_policy__isnull=False).order_by("path")
    names = ", ".join(repr(c.name) for c in allowed) or repr(LOGO_COLLECTION_NAME)
    return f"SVG is for logos and icons only: upload it to the {names} collection."


def svg_collection_move_refusal(collection: Collection, new_parent: Collection) -> str | None:
    """None if `collection` may move under `new_parent`; the reason otherwise (THM.12).

    Moving a collection never saves its images, so `MediaImage.save` cannot refuse it: a
    sub-collection holding SVGs (at any depth) may not leave the SVG collections, while one with
    raster images only moves freely. An `SvgCollection` inside the subtree (the moved one
    included) keeps accepting the SVGs below it wherever it goes."""
    if svg_collection_refusal(new_parent) is None:
        return None
    subtree = list(Collection.objects.descendant_of(collection, inclusive=True))
    svg_roots = tuple(
        SvgCollection.objects.filter(collection__in=subtree).values_list(
            "collection__path", flat=True
        )
    )
    uncovered = [c.pk for c in subtree if not c.path.startswith(svg_roots)]
    svgs = MediaImage.objects.filter(collection__in=uncovered, file__iendswith=".svg")
    if not svgs.exists():
        return None
    return (
        f"{collection.name!r} holds SVG images, which may stay only in the collections for "
        "logos and icons: move them elsewhere first, or choose a parent among those collections."
    )


def check_svg(data: bytes, collection: Collection | None) -> tuple[bytes, None] | tuple[None, str]:
    """(the bytes to store, None) for an SVG allowed in `collection`, else (None, the reason)."""
    refusal = svg_collection_refusal(collection)
    if refusal is not None:
        return None, refusal
    try:
        return sanitise_svg(data), None
    except UnsafeSvgError as error:
        return None, f"This SVG is refused: {error}"


def read_svg(file: Any) -> bytes | None:
    """The bytes of an image file if it is an SVG (up to one byte over `MAX_SVG_BYTES`, which
    the sanitiser refuses), None if it is not or is missing from storage.

    An SVG is markup named `.svg` (`apps.core.uploads.upload_kind`, SEC.2): the content decides,
    so a raster image named `.svg` goes to the raster checks (which refuse its name), and markup
    under another name is refused by them too, never stored. "Named `.svg`" is
    `uploads.is_svg_name`, the same test as the field's, so `logo.svg.` or a `.svg` followed by
    an invisible character cannot be stored as an SVG while skipping the collection rule.
    """
    if not file or not uploads.is_svg_name(file.name or ""):
        return None
    data: bytes
    if file._committed:
        try:
            with file.storage.open(file.name, "rb") as stored:
                data = stored.read(MAX_SVG_BYTES + 1)
        except OSError:
            return None  # missing from storage: nothing that could be served
    else:
        file.open("rb")
        file.seek(0)
        data = file.read(MAX_SVG_BYTES + 1)
        file.seek(0)
    return data if uploads.is_markup(data) else None


class SvgCollection(BaseModel):
    """A Wagtail collection that accepts SVG uploads, with its sub-collections (THM.12).

    SVG is for logos and icons only. The collection is identified by this row, not by its
    display name: Wagtail lets editors rename collections and give two the same name, so a
    name match would let an SVG into any collection called "Logos and icons" and would stop
    SVG uploads when the real one is renamed. The row is written by the 0015 data migration
    only: no admin, no service.
    """

    collection = models.OneToOneField(
        Collection, on_delete=models.CASCADE, related_name="svg_policy"
    )

    class Meta:
        verbose_name = "SVG collection"

    def __str__(self) -> str:
        return f"SVG allowed in {self.collection}"


class MediaImage(AbstractImage):  # type: ignore[misc]  # Wagtail is untyped
    """The site's image model (`WAGTAILIMAGES_IMAGE_MODEL`, spec 10 §10.2).

    Shared by CMS pages, products (CAT.4), vendor logos and banners. It is the image model from
    the first image on (I18N.3), so no image ever has to move between models.

    CMS.8: every image names its licence (from the `ImageLicence` allow list) and author, plus
    the source URL or permission reference its licence requires; `clean` refuses an image
    without them, in the Wagtail admin and in `services.save_image`. The columns accept empty
    values so that images migrated without known metadata can exist: they are listed for
    review and cannot be chosen for new content until classified.

    SEC.2: a new file is checked by its content (`apps.core.uploads`) in `clean`, and the file
    field stores only its re-encoding, without metadata, under a name the app makes.
    """

    file = SanitisedImageField(
        verbose_name="file", upload_to=get_upload_to, width_field="width", height_field="height"
    )
    licence = models.ForeignKey(
        ImageLicence,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="From the allow list: only licences that allow commercial use.",
    )
    author = models.CharField(
        max_length=200, blank=True, help_text="The author or rights holder to credit."
    )
    source_url = models.URLField(
        "source URL",
        max_length=500,
        blank=True,
        help_text="Where the image comes from; not needed for own work or vendor images.",
    )
    credit_text = models.CharField(
        max_length=250, blank=True, help_text="Optional: replaces the generated credit line."
    )
    permission_ref = models.CharField(
        "permission reference",
        max_length=200,
        blank=True,
        help_text="Written permission: reference of the stored permission document.",
    )

    METADATA_FIELDS = ("licence", "author", "source_url", "credit_text", "permission_ref")
    admin_form_fields = (*Image.admin_form_fields, *METADATA_FIELDS)

    class Meta(AbstractImage.Meta):  # type: ignore[misc]
        verbose_name = "image"
        verbose_name_plural = "images"

    def _terms(self) -> LicenceTerms | None:
        licence = self.licence if self.licence_id is not None else None
        return None if licence is None else licence.terms

    def clean(self) -> None:
        super().clean()
        errors = metadata_errors(
            self._terms(),
            author=self.author,
            source_url=self.source_url,
            permission_ref=self.permission_ref,
        )
        file_error = self._new_file_error() or self._clean_svg()
        if file_error:
            errors["file"] = file_error
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        if update_fields is None or {"file", "collection"} & set(update_fields):
            self._enforce_svg_policy()
        # Store a new upload (re-encoded or re-serialised, `SanitisedImageFieldFile.save`) after
        # the SVG policy (so a refused SVG never reaches storage) and before Django collects the
        # column values, so that size, hash and dimensions are those of the stored file.
        if self.file and not getattr(self.file, "_committed", True):
            self.file.save(self.file.name, self.file.file, save=False)
        super().save(*args, **kwargs)

    def _new_file_error(self) -> str | None:
        """Why a newly uploaded file is refused by its content (SEC.2); None when it is fine,
        not new, or an SVG named `.svg` (checked by `_clean_svg`, THM.12)."""
        if not self.file or getattr(self.file, "_committed", True):
            return None
        limits = image_limits()
        data = read_upload(self.file.file, max(limits.max_bytes, MAX_SVG_BYTES))
        try:
            if uploads.upload_kind(data, self.file.name) == "svg":
                return None
            uploads.check_image(data[: limits.max_bytes + 1], self.file.name, limits)
        except uploads.UploadRefused as refusal:
            return refusal.message
        return None

    def _svg_check(self) -> tuple[bytes, bytes | None, str | None] | None:
        """None if the file is not an SVG; else (its bytes, the bytes to store, the reason it is
        refused), exactly one of the last two set."""
        data = read_svg(self.file)
        if data is None:
            return None
        cleaned, refusal = check_svg(data, self.collection if self.collection_id else None)
        return data, cleaned, refusal

    def _clean_svg(self) -> str | None:
        """THM.12: an SVG must sit in an `SvgCollection` (the logos collection, or one below
        it) and pass `apps.core.svg`; a new upload is replaced by its re-serialisation. The
        reason it is refused, or None."""
        check = self._svg_check()
        if check is None:
            return None
        _, cleaned, refusal = check
        file: Any = self.file
        if cleaned is not None and not file._committed:
            file.file = ContentFile(cleaned, name=posixpath.basename(file.name))  # saved on save()
        return refusal

    def _enforce_svg_policy(self) -> None:
        """The same rule on every save, whatever the path: `clean()` is not called by Wagtail's
        multiple upload, which writes the uploaded bytes to storage before the model sees them
        and lets the second step pick another collection, nor by services or the shell. A
        refused SVG raises; one not stored in its re-serialised form is rewritten."""
        check = self._svg_check()
        if check is None:
            return
        data, cleaned, refusal = check
        if cleaned is None:
            raise ValidationError({"file": refusal})
        if cleaned == data:
            return
        file: Any = self.file
        if file._committed:
            name, storage = file.name, file.storage
            file.close()
            storage.delete(name)
            file.name = storage.save(name, ContentFile(cleaned))
            file._file = None  # reopened from storage on the next read
        else:
            file.file = ContentFile(cleaned, name=posixpath.basename(file.name))
        self._set_image_file_metadata()

    @property
    def needs_review(self) -> bool:
        """Migrated without its licence or author: to classify before any new use."""
        return self.licence_id is None or not self.author.strip()

    @property
    def credit(self) -> Credit | None:
        """The credit to show with the image; None when its licence needs no attribution."""
        return credit_for(
            self._terms(),
            author=self.author,
            source_url=self.source_url,
            credit_text=self.credit_text,
        )


class MediaImageRendition(AbstractRendition):  # type: ignore[misc]
    """A resized copy of a `MediaImage`, made on first use by templates."""

    image = models.ForeignKey(MediaImage, on_delete=models.CASCADE, related_name="renditions")

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["image", "filter_spec", "focal_point_key"],
                name="core_one_rendition_per_filter",
            ),
        ]


# --- Transactional email (ARC.16)


class OutboxEmail(BaseModel):
    """One transactional email, from the request to send it until the provider accepts it.

    Written by `apps.core.services.enqueue_email` (which queues the `core.send_email` job in the
    same transaction) and `deliver_email`; every status change adds an `OutboxEmailEvent`. The
    context is a snapshot of the values the templates need, encrypted at rest: it can hold a
    one-time code or personal data. `idempotency_key` (optional, chosen by the caller, e.g.
    `order:<public id>:confirmation`) makes a repeated request a no-op.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RETRYING = "retrying", "Waiting for a retry"
        SENT = "sent", "Accepted by the provider"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Not sent: address suppressed"

    #: No further send attempt for these.
    FINAL: ClassVar[frozenset[str]] = frozenset({Status.SENT, Status.FAILED, Status.SUPPRESSED})

    recipient = models.EmailField(max_length=254)
    template_key = models.CharField(max_length=100)
    language = LanguageField()
    context = EncryptedTextField(blank=True, help_text="JSON snapshot of the template context.")
    idempotency_key = models.CharField(max_length=200, unique=True, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=200, blank=True, db_index=True)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["queued", "retrying", "sent", "failed", "suppressed"]
                ),
                name="core_outboxemail_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.template_key} to {self.recipient} ({self.status})"

    @property
    def context_data(self) -> dict[str, Any]:
        data = json.loads(self.context or "{}")
        return data if isinstance(data, dict) else {}


class OutboxEmailEvent(models.Model):
    """Append-only history of an outbox email: each status change and provider feedback.

    The database refuses updates and deletes (trigger), like the audit log.
    """

    class Kind(models.TextChoices):
        QUEUED = "queued", "Queued"
        RETRYING = "retrying", "Transient failure, retry scheduled"
        SENT = "sent", "Accepted by the provider"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Not sent: address suppressed"
        BOUNCED = "bounced", "Hard bounce reported"
        SOFT_BOUNCED = "soft_bounced", "Soft bounce reported"
        COMPLAINED = "complained", "Complaint reported"

    email = models.ForeignKey(OutboxEmail, on_delete=models.PROTECT, related_name="events")
    status = models.CharField(max_length=16, choices=Kind.choices)
    attempt = models.PositiveIntegerField(default=0)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.status} (attempt {self.attempt})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("OutboxEmailEvent is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("OutboxEmailEvent is append-only")


class EmailSuppression(BaseModel):
    """An address that receives no more email: it hard-bounced or its owner complained.

    Written by `apps.core.services.record_email_feedback` (audited). The address is stored
    normalised (`apps.core.mail.normalise_address`).
    """

    class Reason(models.TextChoices):
        HARD_BOUNCE = "hard_bounce", "Hard bounce"
        COMPLAINT = "complaint", "Complaint"

    address = models.CharField(max_length=254, unique=True)
    reason = models.CharField(max_length=16, choices=Reason.choices)
    detail = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reason__in=["hard_bounce", "complaint"]),
                name="core_emailsuppression_reason_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.address} ({self.reason})"
