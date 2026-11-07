"""Pure vendor rules (no ORM): status state machine, slug generation, member roles and their
permissions, and the legal and e-invoicing identity (validators, required fields, FatturaPA
party block)."""

import dataclasses
import re
from collections.abc import Collection, Container, Iterable, Mapping, Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from django.utils.text import slugify

from apps.core.countries import ISO_3166_ALPHA2, is_eu

# --- Status state machine (spec 04 §4.2)

APPLICANT: Final = "applicant"
ONBOARDING: Final = "onboarding"
ACTIVE: Final = "active"
SUSPENDED: Final = "suspended"
CLOSED: Final = "closed"

STATUSES: Final = (APPLICANT, ONBOARDING, ACTIVE, SUSPENDED, CLOSED)

# Forward along the lifecycle, reinstatement after a suspension, and closure from any open
# status (a rejected application is closed too). Closed is terminal.
TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    APPLICANT: frozenset({ONBOARDING, CLOSED}),
    ONBOARDING: frozenset({ACTIVE, CLOSED}),
    ACTIVE: frozenset({SUSPENDED, CLOSED}),
    SUSPENDED: frozenset({ACTIVE, CLOSED}),
    CLOSED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a vendor status change is not allowed by the state machine."""


def allowed_transitions(status: str) -> frozenset[str]:
    if status not in TRANSITIONS:
        raise InvalidTransition(f"unknown vendor status {status!r}")
    return TRANSITIONS[status]


def check_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS:
        raise InvalidTransition(f"unknown vendor status {target!r}")
    if target not in allowed_transitions(current):
        raise InvalidTransition(f"vendor status change {current} → {target} is not allowed")


# --- Slug

SLUG_MAX_LENGTH: Final = 80
SLUG_FALLBACK: Final = "vendor"


def unique_slug(display_name: str, taken: Container[str], max_length: int = SLUG_MAX_LENGTH) -> str:
    """ASCII slug of the display name; `-2`, `-3`… when taken, always within `max_length`."""
    base = slugify(display_name)[:max_length].strip("-") or SLUG_FALLBACK
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[: max_length - len(suffix)].rstrip("-") + suffix
        if candidate not in taken:
            return candidate
        n += 1


# --- Member roles and permissions (spec 02 §2.2, CORE.2)

OWNER: Final = "owner"
STAFF: Final = "staff"

ROLES: Final = (OWNER, STAFF)


class VendorAction(StrEnum):
    """What a vendor member can do on their own vendor, in the vendor area, API and MCP."""

    ORDERS = "orders"
    QUOTES = "quotes"
    PRODUCTS = "products"
    PROFILE = "profile"  # storefront texts, policies, logo
    PAYOUTS = "payouts"
    BANK_ACCOUNT = "bank_account"  # IBAN / payout account
    TAX_REGIME = "tax_regime"  # tax regime change request
    MEMBERS = "members"  # add and remove members, change roles
    API_KEYS = "api_keys"  # issue vendor-bound API keys, which act for the whole vendor (CORE.13)


# The role matrix. Owner: everything about their own vendor, financial settings included.
# Staff: day-to-day operations only; no payouts, IBAN, tax regime, profile or members.
ROLE_PERMISSIONS: Final[Mapping[str, frozenset[VendorAction]]] = {
    OWNER: frozenset(VendorAction),
    STAFF: frozenset({VendorAction.ORDERS, VendorAction.QUOTES, VendorAction.PRODUCTS}),
}


class MembershipError(ValueError):
    """A vendor membership change that the rules refuse."""


class UnknownRole(MembershipError):
    """The role is not one of `ROLES`."""


class UnknownAction(ValueError):
    """The action is not a `VendorAction`."""


class AlreadyMember(MembershipError):
    """The user is already a member of the vendor."""


class NoOwnerLeft(MembershipError):
    """The change would leave the vendor without an owner."""


def check_role(role: str) -> None:
    if role not in ROLE_PERMISSIONS:
        raise UnknownRole(f"unknown vendor member role {role!r}")


def permissions_for(role: str) -> frozenset[VendorAction]:
    check_role(role)
    return ROLE_PERMISSIONS[role]


def to_action(action: str) -> VendorAction:
    try:
        return VendorAction(action)
    except ValueError:
        raise UnknownAction(f"unknown vendor action {action!r}") from None


def is_allowed(role: str, action: str) -> bool:
    """Whether a member with `role` may perform `action` on their own vendor.

    Unknown roles or actions raise instead of answering False, so a typo fails loudly.
    """
    return to_action(action) in permissions_for(role)


def check_keeps_an_owner(roles_after: Collection[str]) -> None:
    """Every vendor with members keeps at least one owner after a membership change.

    `roles_after` holds the roles of the vendor's members once the change is applied; it also
    means the first member of a vendor must be an owner.
    """
    if OWNER not in roles_after:
        raise NoOwnerLeft("a vendor must keep at least one owner")


# --- Legal and e-invoicing identity (VEN.12, spec 04 "Legal and e-invoicing identity")
#
# Every legal field is optional while a profile is edited: what is *required*, and for what
# purpose, comes from staff-editable rules (`LegalFieldRequirement`), passed in as data.

# The jurisdiction whose VAT, codice fiscale and FatturaPA/SDI rules this section implements.
# It is the vendor's country of establishment, never the platform's country (ARC.7).
ITALY: Final = "IT"  # country-literal: Italian rules for vendors established in Italy

SOLE_PROPRIETORSHIP: Final = "sole_proprietorship"
PARTNERSHIP: Final = "partnership"
SRL: Final = "srl"
SPA: Final = "spa"
COOPERATIVE: Final = "cooperative"
OTHER_LEGAL_FORM: Final = "other"

LEGAL_FORMS: Final = (SOLE_PROPRIETORSHIP, PARTNERSHIP, SRL, SPA, COOPERATIVE, OTHER_LEGAL_FORM)
# Forms registered in the company register as companies (FatturaPA `IscrizioneREA`).
COMPANY_FORMS: Final = frozenset({PARTNERSHIP, SRL, SPA, COOPERATIVE})

# FatturaPA 1.2.x `RegimeFiscale`, with the official labels of the SDI specification.
# There is no RF03 (it was removed from the specification).
FISCAL_REGIMES: Final[Mapping[str, str]] = {
    "RF01": "Ordinario",
    "RF02": "Contribuenti minimi (art.1, c.96-117, L. 244/07)",
    "RF04": "Agricoltura e attività connesse e pesca (artt.34 e 34-bis, DPR 633/72)",
    "RF05": "Vendita sali e tabacchi (art.74, c.1, DPR. 633/72)",
    "RF06": "Commercio fiammiferi (art.74, c.1, DPR 633/72)",
    "RF07": "Editoria (art.74, c.1, DPR 633/72)",
    "RF08": "Gestione servizi telefonia pubblica (art.74, c.1, DPR 633/72)",
    "RF09": "Rivendita documenti di trasporto pubblico e di sosta (art.74, c.1, DPR 633/72)",
    "RF10": (
        "Intrattenimenti, giochi e altre attività di cui alla tariffa allegata al DPR 640/72 "
        "(art.74, c.6, DPR 633/72)"
    ),
    "RF11": "Agenzie viaggi e turismo (art.74-ter, DPR 633/72)",
    "RF12": "Agriturismo (art.5, c.2, L. 413/91)",
    "RF13": "Vendite a domicilio (art.25-bis, c.6, DPR 600/73)",
    "RF14": (
        "Rivendita beni usati, oggetti d'arte, d'antiquariato o da collezione (art.36, DL 41/95)"
    ),
    "RF15": (
        "Agenzie di vendite all'asta di oggetti d'arte, antiquariato o da collezione "
        "(art.40-bis, DL 41/95)"
    ),
    "RF16": "IVA per cassa P.A. (art.6, c.5, DPR 633/72)",
    "RF17": "IVA per cassa (art. 32-bis, DL 83/2012)",
    "RF18": "Altro",
    "RF19": "Regime forfettario (art.1, c.54-89, L. 190/2014)",
}
FLAT_RATE_REGIME: Final = "RF19"  # forfettario

SOLE_SHAREHOLDER: Final = "SU"
SEVERAL_SHAREHOLDERS: Final = "SM"
IN_LIQUIDATION: Final = "LS"
NOT_IN_LIQUIDATION: Final = "LN"

# Official two-letter codes of the Italian provinces and metropolitan cities.
ITALIAN_PROVINCES: Final = frozenset(
    """
    AG AL AN AO AP AQ AR AT AV BA BG BI BL BN BO BR BS BT BZ CA CB CE CH CL CN CO CR CS CT CZ
    EN FC FE FG FI FM FR GE GO GR IM IS KR LC LE LI LO LT LU MB MC ME MI MN MO MS MT NA NO NU
    OR PA PC PD PE PG PI PN PO PR PT PU PV PZ RA RC RE RG RI RM RN RO SA SI SO SP SR SS SU SV
    TA TE TN TO TP TR TS TV UD VA VB VC VE VI VR VT VV
    """.split()  # noqa: SIM905 - compact, reviewable table
)
# Sardinian codes of the 2016 and later reorganisations: registrations still carry them.
FORMER_ITALIAN_PROVINCES: Final = frozenset({"CI", "OG", "OT", "VS"})
PROVINCE_CODES: Final = ITALIAN_PROVINCES | FORMER_ITALIAN_PROVINCES

SDI_PEC_CODE: Final = "0000000"  # invoices are delivered to the PEC address
SDI_FOREIGN_CODE: Final = "XXXXXXX"  # recipient established outside Italy

# VIES status of the VAT number (checked through `apps.vendors.vies.VatNumberValidator`).
VIES_UNCHECKED: Final = "unchecked"
VIES_VALID: Final = "valid"
VIES_INVALID: Final = "invalid"
VIES_UNAVAILABLE: Final = "unavailable"
VIES_STATUSES: Final = (VIES_UNCHECKED, VIES_VALID, VIES_INVALID, VIES_UNAVAILABLE)
VIES_RESULTS: Final = frozenset({VIES_VALID, VIES_INVALID, VIES_UNAVAILABLE})

# VAT prefixes that differ from the ISO country code.
VAT_PREFIXES: Final[Mapping[str, str]] = {"GR": "EL"}
# Countries whose VAT number starts with letters that belong to the number itself, so they are
# never stripped as a country prefix: the Swiss VAT number is the UID "CHE" + 9 digits, and its
# FatturaPA IdCodice keeps "CHE" (stripping "CH" would leave "E123456789").
VAT_NUMBER_LETTERS: Final[Mapping[str, str]] = {"CH": "CHE"}

# IBAN lengths of the SEPA countries; other countries only get the generic 15-34 check.
IBAN_LENGTHS: Final[Mapping[str, int]] = {
    entry[:2]: int(entry[2:])
    for entry in """
    AD24 AT20 BE16 BG22 CH21 CY28 CZ24 DE22 DK18 EE20 ES24 FI18 FR27 GB22 GI23 GR27 HR21 HU28
    IE22 IS26 IT27 LI21 LT20 LU20 LV21 MC27 MT31 NL18 NO15 PL28 PT25 RO24 SE24 SI19 SK24 SM27
    VA22
    """.split()  # noqa: SIM905 - compact, reviewable table
}

_ITALIAN_VAT = re.compile(r"\d{11}", re.ASCII)
_OMOCODIA_DIGIT = "[0-9LMNPQRSTUV]"
_CODICE_FISCALE = re.compile(
    rf"[A-Z]{{6}}{_OMOCODIA_DIGIT}{{2}}[ABCDEHLMPRST]{_OMOCODIA_DIGIT}{{2}}"
    rf"[A-Z]{_OMOCODIA_DIGIT}{{3}}[A-Z]",
    re.ASCII,
)
_OMOCODIA = str.maketrans("LMNPQRSTUV", "0123456789")
_ALPHANUMERIC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Codice fiscale check character: values of the characters in odd (1st, 3rd…) positions.
_CF_ODD_DIGITS = (1, 0, 5, 7, 9, 13, 15, 17, 19, 21)  # digits 0-9 count as letters A-J
_CF_ODD_LETTERS = (*_CF_ODD_DIGITS, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23)
_CF_ODD: Final = dict(zip(_ALPHANUMERIC, _CF_ODD_DIGITS + _CF_ODD_LETTERS, strict=True))
_CF_EVEN: Final = {c: (int(c) if c.isdigit() else ord(c) - ord("A")) for c in _ALPHANUMERIC}
_IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", re.ASCII)
_ITALIAN_IBAN = re.compile(r"IT\d{2}[A-Z]\d{10}[A-Z0-9]{12}", re.ASCII)
_BIC = re.compile(r"[A-Z]{4}([A-Z]{2})[A-Z0-9]{2}([A-Z0-9]{3})?", re.ASCII)
_SDI_CODE = re.compile(r"[A-Z0-9]{7}", re.ASCII)
_EORI = re.compile(r"[A-Z]{2}[A-Z0-9]{11,15}", re.ASCII)  # FatturaPA CodEORI: 13-17 characters
_ATECO = re.compile(r"\d{2}(\.\d{1,2}){0,2}", re.ASCII)
_ITALIAN_POSTAL_CODE = re.compile(r"\d{5}", re.ASCII)
_REA_NUMBER = re.compile(r"[A-Z0-9]{1,20}", re.ASCII)
_VAT_CODE = re.compile(r"[A-Z0-9]{2,15}", re.ASCII)


def compact(value: str) -> str:
    """Upper case without whitespace: how identifiers are stored and compared."""
    return "".join(value.split()).upper()


def vat_prefix(country: str) -> str:
    """The VAT (and VIES) prefix of a country: its ISO code, except EL for Greece."""
    return VAT_PREFIXES.get(country, country)


def split_vat_number(vat_number: str, country: str) -> tuple[str, str]:
    """(country, number) of a VAT number, with the country's prefix removed if present.

    The letters of `VAT_NUMBER_LETTERS` (Swiss "CHE") are part of the number and are kept.
    """
    code = compact(vat_number)
    letters = VAT_NUMBER_LETTERS.get(country)
    if letters and code.startswith(letters):
        return country, code
    for prefix in (vat_prefix(country), country):
        if prefix and code.startswith(prefix) and len(code) > len(prefix):
            return country, code[len(prefix) :]
    return country, code


def italian_vat_check_digit(first_ten: str) -> str:
    """Check digit of an Italian VAT number (partita IVA): Luhn over the first ten digits."""
    total = 0
    for position, char in enumerate(first_ten):
        digit = int(char)
        if position % 2:
            digit *= 2
            digit -= 9 if digit > 9 else 0
        total += digit
    return str((10 - total % 10) % 10)


def is_valid_italian_vat(value: str) -> bool:
    """11 digits with a valid check digit; an `IT` prefix and spaces are accepted."""
    code = compact(value).removeprefix(ITALY)
    return bool(_ITALIAN_VAT.fullmatch(code)) and italian_vat_check_digit(code[:10]) == code[10]


def codice_fiscale_check_char(first_fifteen: str) -> str:
    """Check character of a 16-character codice fiscale, from its first fifteen characters."""
    total = sum(
        (_CF_EVEN if position % 2 else _CF_ODD)[char] for position, char in enumerate(first_fifteen)
    )
    return chr(ord("A") + total % 26)


def is_valid_codice_fiscale(value: str) -> bool:
    """A personal codice fiscale (omocodia included) or the 11-digit numeric form."""
    code = compact(value)
    if _ITALIAN_VAT.fullmatch(code):
        return is_valid_italian_vat(code)  # entities: same check digit as the partita IVA
    if not _CODICE_FISCALE.fullmatch(code):
        return False
    day = int(code[9:11].translate(_OMOCODIA))  # women: day + 40
    if not (1 <= day <= 31 or 41 <= day <= 71):
        return False
    return codice_fiscale_check_char(code[:15]) == code[15]


def _iban_remainder(text: str) -> int:
    return int("".join(str(int(char, 36)) for char in text)) % 97


def iban_check_digits(country: str, bban: str) -> str:
    """The two check digits of the IBAN made of `country` and `bban` (ISO 13616, mod 97)."""
    return f"{98 - _iban_remainder(compact(bban) + country + '00'):02d}"


def is_valid_iban(value: str) -> bool:
    """Structure, length per country and mod-97 checksum of an IBAN (spaces accepted)."""
    code = compact(value)
    if not _IBAN.fullmatch(code):
        return False
    country = code[:2]
    expected = IBAN_LENGTHS.get(country)
    if expected is None and country not in ISO_3166_ALPHA2:
        return False
    if expected is not None and len(code) != expected:
        return False
    if country == ITALY and not _ITALIAN_IBAN.fullmatch(code):
        return False
    return _iban_remainder(code[4:] + code[:4]) == 1


def is_valid_bic(value: str) -> bool:
    """A BIC (ISO 9362): 8 or 11 characters with a valid country code."""
    match = _BIC.fullmatch(compact(value))
    return match is not None and match.group(1) in ISO_3166_ALPHA2


def sdi_code_error(code: str, *, country: str) -> str | None:
    """Why `code` cannot be a vendor's SDI recipient code, or None if it can."""
    code = compact(code)
    if len(code) == 6:
        return (
            "6-character codes identify public administration offices; "
            "a vendor's code has 7 characters."
        )
    if not _SDI_CODE.fullmatch(code):
        return "The SDI recipient code has 7 letters or digits."
    if code == SDI_FOREIGN_CODE and country == ITALY:
        return f"{SDI_FOREIGN_CODE} is only for vendors established outside Italy."
    return None


def is_valid_province(code: str) -> bool:
    return code in PROVINCE_CODES


def is_valid_eori(value: str) -> bool:
    return bool(_EORI.fullmatch(compact(value)))


def is_valid_ateco(value: str) -> bool:
    return bool(_ATECO.fullmatch(value.strip()))


@dataclasses.dataclass(frozen=True)
class LegalIdentity:
    """A vendor's legal and e-invoicing identity, as plain values (field names = model fields)."""

    country: str
    legal_name: str = ""
    legal_form: str = ""
    first_name: str = ""
    last_name: str = ""
    vat_number: str = ""
    tax_code: str = ""
    tax_residence_country: str = ""
    fiscal_regime: str = ""
    ateco_code: str = ""
    eori: str = ""
    registered_street: str = ""
    registered_street_number: str = ""
    registered_postal_code: str = ""
    registered_city: str = ""
    registered_province: str = ""
    registered_country: str = ""
    rea_office: str = ""
    rea_number: str = ""
    share_capital: Decimal | None = None
    sole_shareholder: str = ""
    liquidation_status: str = ""
    sdi_recipient_code: str = ""
    pec: str = ""
    fiscal_representative_name: str = ""
    fiscal_representative_vat_number: str = ""
    establishment_street: str = ""
    establishment_street_number: str = ""
    establishment_postal_code: str = ""
    establishment_city: str = ""
    establishment_province: str = ""
    establishment_country: str = ""
    iban: str = ""
    bic: str = ""
    bank_name: str = ""
    contact_email: str = ""
    phone: str = ""

    def values(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# Fields a `LegalFieldRequirement` may name. Country, legal name and contact email are always
# present on a vendor; the phone is not a legal field.
LEGAL_FIELDS: Final = tuple(
    f.name
    for f in dataclasses.fields(LegalIdentity)
    if f.name not in {"country", "legal_name", "contact_email", "phone"}
)

# Identifiers stored compact (upper case, no spaces).
COMPACT_FIELDS: Final = (
    "vat_number",
    "tax_code",
    "eori",
    "rea_number",
    "sdi_recipient_code",
    "fiscal_representative_vat_number",
    "iban",
    "bic",
)


def _address_country(identity: LegalIdentity, prefix: str) -> str:
    country: str = getattr(identity, f"{prefix}_country")
    return country or (identity.country if prefix == "registered" else "")


def normalized(identity: LegalIdentity) -> LegalIdentity:
    """Identifiers compacted; Italian province codes in upper case."""
    changes: dict[str, Any] = {name: compact(getattr(identity, name)) for name in COMPACT_FIELDS}
    for prefix in ("registered", "establishment"):
        province = getattr(identity, f"{prefix}_province")
        if _address_country(identity, prefix) == ITALY:
            changes[f"{prefix}_province"] = compact(province)
    changes["pec"] = identity.pec.strip()
    return dataclasses.replace(identity, **changes)


def _address_errors(identity: LegalIdentity, prefix: str) -> dict[str, list[str]]:
    if _address_country(identity, prefix) != ITALY:
        return {}
    errors: dict[str, list[str]] = {}
    postal_code = getattr(identity, f"{prefix}_postal_code")
    if postal_code and not _ITALIAN_POSTAL_CODE.fullmatch(postal_code):
        errors[f"{prefix}_postal_code"] = ["An Italian postal code (CAP) has 5 digits."]
    province = getattr(identity, f"{prefix}_province")
    if province and not is_valid_province(compact(province)):
        errors[f"{prefix}_province"] = [f"{province!r} is not an Italian province code (e.g. RM)."]
    return errors


def identity_errors(identity: LegalIdentity) -> dict[str, list[str]]:
    """Invalid values, by field. Empty values are never errors: every field is optional here."""
    ident = normalized(identity)
    italian = ident.country == ITALY
    errors: dict[str, list[str]] = {}

    def check(name: str, ok: bool, message: str) -> None:
        if getattr(ident, name) and not ok:
            errors[name] = [message]

    if italian:
        check(
            "vat_number",
            is_valid_italian_vat(ident.vat_number),
            "Not a valid Italian VAT number (11 digits with a check digit).",
        )
        check(
            "tax_code",
            is_valid_codice_fiscale(ident.tax_code),
            "Not a valid codice fiscale (16 characters, or 11 digits for entities).",
        )
    else:
        _, number = split_vat_number(ident.vat_number, ident.country)
        check("vat_number", bool(_VAT_CODE.fullmatch(number)), "Not a valid VAT number.")
    check("fiscal_regime", ident.fiscal_regime in FISCAL_REGIMES, "Unknown fiscal regime.")
    check("legal_form", ident.legal_form in LEGAL_FORMS, "Unknown legal form.")
    check("ateco_code", is_valid_ateco(ident.ateco_code), "An ATECO code looks like 47.91.10.")
    check("eori", is_valid_eori(ident.eori), "An EORI number is a country code + 11-15 characters.")
    check("rea_office", is_valid_province(ident.rea_office), "Not an Italian province code.")
    check(
        "rea_number", bool(_REA_NUMBER.fullmatch(ident.rea_number)), "Up to 20 letters or digits."
    )
    check(
        "sole_shareholder",
        ident.sole_shareholder in {SOLE_SHAREHOLDER, SEVERAL_SHAREHOLDERS},
        "SU (sole shareholder) or SM (several shareholders).",
    )
    check(
        "liquidation_status",
        ident.liquidation_status in {IN_LIQUIDATION, NOT_IN_LIQUIDATION},
        "LS (in liquidation) or LN (not in liquidation).",
    )
    sdi_error = sdi_code_error(ident.sdi_recipient_code, country=ident.country)
    check("sdi_recipient_code", sdi_error is None, sdi_error or "")
    check(
        "fiscal_representative_vat_number",
        is_valid_italian_vat(ident.fiscal_representative_vat_number),
        "The fiscal representative needs a valid Italian VAT number.",
    )
    check("iban", is_valid_iban(ident.iban), "Not a valid IBAN (check digits or length).")
    check("bic", is_valid_bic(ident.bic), "A BIC has 8 or 11 characters, e.g. ABCDITMMXXX.")
    if ident.share_capital is not None and ident.share_capital < 0:
        errors["share_capital"] = ["The share capital cannot be negative."]
    errors.update(_address_errors(ident, "registered"))
    errors.update(_address_errors(ident, "establishment"))
    return errors


# --- Required legal fields (rules are data: `LegalFieldRequirement`)

PAYOUT: Final = "payout"
PURPOSES: Final = (PAYOUT,)

# Regions a rule can target instead of one country (empty: every vendor).
EU: Final = "eu"
NON_EU: Final = "non_eu"
REGIONS: Final = (EU, NON_EU)


def region_of(country: str) -> str:
    return EU if is_eu(country) else NON_EU


class UnknownLegalField(ValueError):
    """A requirement names a field that is not one of `LEGAL_FIELDS`."""


@dataclasses.dataclass(frozen=True)
class Requirement:
    """`field` is required for `purpose` from vendors of `country` with `legal_form`.

    An empty country, region or legal form matches every vendor; `region` (EU or NON_EU) matches
    every vendor established inside or outside the EU.
    """

    field: str
    country: str = ""
    legal_form: str = ""
    purpose: str = PAYOUT
    region: str = ""


def required_fields(
    values: Mapping[str, Any], rules: Iterable[Requirement], *, purpose: str = PAYOUT
) -> list[str]:
    """The fields the rules require from this vendor, in `LEGAL_FIELDS` order.

    Rules are additive: every rule matching the vendor's country and legal form applies (there
    is no "most specific wins"); staff relax a requirement by deactivating its rule. While the
    legal form is unknown, a rule for a specific form cannot be decided, so the legal form
    itself becomes required.
    """
    country = str(values.get("country") or "")
    legal_form = str(values.get("legal_form") or "")
    required: set[str] = set()
    for rule in rules:
        if rule.field not in LEGAL_FIELDS:
            raise UnknownLegalField(f"{rule.field!r} is not a legal field")
        if rule.purpose != purpose or rule.country not in ("", country):
            continue
        if rule.region and (not country or rule.region != region_of(country)):
            continue
        if rule.legal_form and not legal_form:
            required.add("legal_form")
        elif rule.legal_form in ("", legal_form):
            required.add(rule.field)
    return [name for name in LEGAL_FIELDS if name in required]


def delivery_channel_ok(values: Mapping[str, Any]) -> bool:
    """The vendor can receive e-invoices: a 7-character SDI code, or 0000000 and a PEC."""
    code = compact(str(values.get("sdi_recipient_code") or ""))
    if not code or sdi_code_error(code, country=str(values.get("country") or "")):
        return False
    return code != SDI_PEC_CODE or bool(str(values.get("pec") or "").strip())


def is_filled(name: str, values: Mapping[str, Any]) -> bool:
    if name == "sdi_recipient_code":
        return delivery_channel_ok(values)
    value = values.get(name)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def missing_required_fields(
    values: Mapping[str, Any], rules: Iterable[Requirement], *, purpose: str = PAYOUT
) -> list[str]:
    """Required fields (see `required_fields`) that the vendor has not filled in."""
    return [
        name
        for name in required_fields(values, rules, purpose=purpose)
        if not is_filled(name, values)
    ]


@dataclasses.dataclass(frozen=True)
class Completeness:
    """Required fields that are missing, and required fields holding an invalid value."""

    missing: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing and not self.invalid


def completeness(
    identity: LegalIdentity, rules: Iterable[Requirement], *, purpose: str = PAYOUT
) -> Completeness:
    values = identity.values()
    rules = list(rules)
    missing = missing_required_fields(values, rules, purpose=purpose)
    errors = identity_errors(identity)
    invalid = [
        name
        for name in required_fields(values, rules, purpose=purpose)
        if name in errors and name not in missing
    ]
    return Completeness(missing=tuple(missing), invalid=tuple(invalid))


# --- DAC7 dataset (spec 11 CMP.1). The seeded payout requirements include it for every vendor.

VAT_OR_TIN: Final = "vat_number or tax_code"


def dac7_missing(identity: LegalIdentity) -> tuple[str, ...]:
    """What the vendor's DAC7 dataset still lacks: legal name, VAT number or TIN, country of tax
    residence, registered office (street, postal code, city, country; province in Italy), IBAN.

    A permanent establishment is reported only when the vendor has one, so it is never missing.
    """
    ident = normalized(identity)
    missing: list[str] = []
    if not ident.legal_name.strip():
        missing.append("legal_name")
    if not (ident.vat_number or ident.tax_code):
        missing.append(VAT_OR_TIN)
    if not ident.tax_residence_country:
        missing.append("tax_residence_country")
    for name in ("street", "postal_code", "city"):
        if not str(getattr(ident, f"registered_{name}")).strip():
            missing.append(f"registered_{name}")
    if ident.registered_country == ITALY and not ident.registered_province:
        missing.append("registered_province")
    if not ident.registered_country:
        missing.append("registered_country")
    if not ident.iban:
        missing.append("iban")
    return tuple(missing)


class PayoutNotAllowed(ValueError):
    """The vendor cannot request a payout: required legal fields are missing or invalid."""

    def __init__(self, vendor: str, result: Completeness) -> None:
        self.missing = result.missing
        self.invalid = result.invalid
        parts = []
        if result.missing:
            parts.append("missing " + ", ".join(result.missing))
        if result.invalid:
            parts.append("invalid " + ", ".join(result.invalid))
        super().__init__(
            f"{vendor} cannot request a payout until its legal profile is complete: "
            + "; ".join(parts)
            + "."
        )


# --- FatturaPA 1.2.x party blocks (data only: CMP.2 writes the XML)

CEDENTE: Final = "CedentePrestatore"  # the vendor invoices (its customers)
CESSIONARIO: Final = "CessionarioCommittente"  # the vendor is invoiced (platform commissions)
FOREIGN_POSTAL_CODE: Final = "00000"
_REA_FIELDS: Final = (
    "rea_office",
    "rea_number",
    "share_capital",
    "sole_shareholder",
    "liquidation_status",
)


class PartyBlockError(ValueError):
    """The identity cannot fill a valid FatturaPA block; `problems` names fields and rules."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("Cannot fill the FatturaPA block: " + "; ".join(self.problems) + ".")


class _Block:
    """Collects FatturaPA values, checking presence and length against the 1.2.x rules."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def text(
        self, field: str, element: str, value: str, *, max_length: int, min_length: int = 1
    ) -> str:
        if not value:
            self.problems.append(f"{field}: {element} is mandatory")
        elif not min_length <= len(value) <= max_length:
            self.problems.append(f"{field}: {element} takes {min_length}-{max_length} characters")
        return value

    def identifiers(self, ident: LegalIdentity, names: Iterable[str]) -> None:
        """Report the `identity_errors` of the emitted `names` (checksums, codes, formats)."""
        errors = identity_errors(ident)
        for name in names:
            self.problems.extend(f"{name}: {message}" for message in errors.get(name, ()))


def _id_fiscale_iva(block: _Block, field: str, vat_number: str, country: str) -> dict[str, str]:
    paese, codice = split_vat_number(vat_number, country)
    return {
        "IdPaese": block.text("country", "IdPaese", paese, max_length=2, min_length=2),
        "IdCodice": block.text(field, "IdCodice", codice, max_length=28),
    }


def _anagrafica(block: _Block, ident: LegalIdentity) -> dict[str, str]:
    anagrafica: dict[str, str]
    if ident.legal_form == SOLE_PROPRIETORSHIP and ident.first_name and ident.last_name:
        anagrafica = {
            "Nome": block.text("first_name", "Nome", ident.first_name, max_length=60),
            "Cognome": block.text("last_name", "Cognome", ident.last_name, max_length=60),
        }
    else:
        anagrafica = {
            "Denominazione": block.text(
                "legal_name", "Denominazione", ident.legal_name, max_length=80
            )
        }
    if ident.eori:
        anagrafica["CodEORI"] = block.text(
            "eori", "CodEORI", ident.eori, max_length=17, min_length=13
        )
    return anagrafica


def _sede(block: _Block, ident: LegalIdentity, prefix: str) -> dict[str, str]:
    """`Sede` / `StabileOrganizzazione`: foreign postal codes become 00000, Provincia is Italian."""

    def value(name: str) -> str:
        return str(getattr(ident, f"{prefix}_{name}")).strip()

    nazione = _address_country(ident, prefix)
    postal_code = value("postal_code")
    if nazione != ITALY and not _ITALIAN_POSTAL_CODE.fullmatch(postal_code):
        postal_code = FOREIGN_POSTAL_CODE
    sede = {
        "Indirizzo": block.text(f"{prefix}_street", "Indirizzo", value("street"), max_length=60)
    }
    if value("street_number"):
        sede["NumeroCivico"] = block.text(
            f"{prefix}_street_number", "NumeroCivico", value("street_number"), max_length=8
        )
    sede["CAP"] = block.text(
        f"{prefix}_postal_code", "CAP", postal_code, max_length=5, min_length=5
    )
    if postal_code and not _ITALIAN_POSTAL_CODE.fullmatch(postal_code):
        block.problems.append(f"{prefix}_postal_code: CAP takes 5 digits")
    sede["Comune"] = block.text(f"{prefix}_city", "Comune", value("city"), max_length=60)
    if nazione == ITALY and value("province"):
        sede["Provincia"] = block.text(
            f"{prefix}_province", "Provincia", value("province"), max_length=2, min_length=2
        )
    sede["Nazione"] = block.text(
        f"{prefix}_country", "Nazione", nazione, max_length=2, min_length=2
    )
    return sede


def _iscrizione_rea(block: _Block, ident: LegalIdentity) -> dict[str, str]:
    rea = {
        "Ufficio": block.text(
            "rea_office", "Ufficio", ident.rea_office, max_length=2, min_length=2
        ),
        "NumeroREA": block.text("rea_number", "NumeroREA", ident.rea_number, max_length=20),
    }
    if ident.share_capital is not None:
        capital = f"{ident.share_capital.quantize(Decimal('0.01'))}"
        rea["CapitaleSociale"] = block.text(
            "share_capital", "CapitaleSociale", capital, max_length=15, min_length=4
        )
    if ident.sole_shareholder:
        rea["SocioUnico"] = ident.sole_shareholder
    rea["StatoLiquidazione"] = block.text(
        "liquidation_status", "StatoLiquidazione", ident.liquidation_status, max_length=2
    )
    return rea


def _contatti(ident: LegalIdentity) -> dict[str, str]:
    """Optional contacts: values outside the FatturaPA lengths are left out, never truncated."""
    contatti = {}
    phone = compact(ident.phone)
    if 5 <= len(phone) <= 12:
        contatti["Telefono"] = phone
    if 7 <= len(ident.contact_email) <= 256:
        contatti["Email"] = ident.contact_email
    return contatti


def fatturapa_party(identity: LegalIdentity, role: str = CEDENTE) -> dict[str, Any]:
    """The FatturaPA 1.2.x `CedentePrestatore` or `CessionarioCommittente` block, as a dict.

    Keys follow the FatturaPA element names and order. Raises `PartyBlockError` listing every
    missing or invalid value (presence and length, plus the `identity_errors` of the emitted
    identifiers: VAT and codice fiscale checksums, EORI, province and REA codes, the fiscal
    representative's VAT number); nothing is truncated or guessed.
    """
    if role not in (CEDENTE, CESSIONARIO):
        raise ValueError(f"unknown FatturaPA party role {role!r}")
    ident = normalized(identity)
    block = _Block()
    # Postal codes and the fiscal regime are checked by the block itself.
    checked = ["vat_number", "tax_code", "eori", "registered_province"]
    if ident.establishment_street:
        checked.append("establishment_province")
    if role == CEDENTE and (ident.rea_office or ident.rea_number):
        checked += _REA_FIELDS
    if role == CESSIONARIO:
        checked.append("fiscal_representative_vat_number")
    block.identifiers(ident, checked)
    dati: dict[str, Any] = {}
    if ident.vat_number:
        dati["IdFiscaleIVA"] = _id_fiscale_iva(block, "vat_number", ident.vat_number, ident.country)
    elif role == CEDENTE:
        block.problems.append("vat_number: IdFiscaleIVA is mandatory for the CedentePrestatore")
    elif role == CESSIONARIO and ident.country != ITALY and ident.tax_code:
        # A foreign vendor without a VAT number is identified by its national TIN.
        dati["IdFiscaleIVA"] = {
            "IdPaese": block.text("country", "IdPaese", ident.country, max_length=2, min_length=2),
            "IdCodice": block.text("tax_code", "IdCodice", ident.tax_code, max_length=28),
        }
    if ident.tax_code and is_valid_codice_fiscale(ident.tax_code):
        dati["CodiceFiscale"] = ident.tax_code  # Italian codes only
    if role == CESSIONARIO and not dati:
        block.problems.append("vat_number or tax_code: IdFiscaleIVA or CodiceFiscale is mandatory")
    dati["Anagrafica"] = _anagrafica(block, ident)
    if role == CEDENTE:
        if ident.fiscal_regime not in FISCAL_REGIMES:
            block.problems.append("fiscal_regime: RegimeFiscale is mandatory (RF01-RF19)")
        dati["RegimeFiscale"] = ident.fiscal_regime

    party: dict[str, Any] = {"DatiAnagrafici": dati, "Sede": _sede(block, ident, "registered")}
    if ident.establishment_street:
        party["StabileOrganizzazione"] = _sede(block, ident, "establishment")
    if role == CEDENTE:
        if ident.rea_office or ident.rea_number:
            party["IscrizioneREA"] = _iscrizione_rea(block, ident)
        if contatti := _contatti(ident):
            party["Contatti"] = contatti
    elif ident.fiscal_representative_name or ident.fiscal_representative_vat_number:
        representative = _representative(block, ident)
        party["RappresentanteFiscale"] = {
            "IdFiscaleIVA": representative["IdFiscaleIVA"],
            **representative["Anagrafica"],
        }
    if block.problems:
        raise PartyBlockError(block.problems)
    return party


def _representative(block: _Block, ident: LegalIdentity) -> dict[str, Any]:
    return {
        "IdFiscaleIVA": _id_fiscale_iva(
            block, "fiscal_representative_vat_number", ident.fiscal_representative_vat_number, ITALY
        ),
        "Anagrafica": {
            "Denominazione": block.text(
                "fiscal_representative_name",
                "Denominazione",
                ident.fiscal_representative_name,
                max_length=80,
            )
        },
    }


def fatturapa_fiscal_representative(identity: LegalIdentity) -> dict[str, Any] | None:
    """The header `RappresentanteFiscale` of a vendor invoicing as CedentePrestatore, if any."""
    ident = normalized(identity)
    if not (ident.fiscal_representative_name or ident.fiscal_representative_vat_number):
        return None
    block = _Block()
    block.identifiers(ident, ["fiscal_representative_vat_number"])
    representative = {"DatiAnagrafici": _representative(block, ident)}
    if block.problems:
        raise PartyBlockError(block.problems)
    return representative


def fatturapa_transmission(identity: LegalIdentity) -> dict[str, str]:
    """Where SDI delivers invoices addressed to the vendor: `CodiceDestinatario`, PEC if 0000000."""
    ident = normalized(identity)
    if not delivery_channel_ok(ident.values()):
        raise PartyBlockError(
            ["sdi_recipient_code: a 7-character SDI code, or 0000000 with a PEC, is mandatory"]
        )
    transmission = {"CodiceDestinatario": ident.sdi_recipient_code}
    if ident.sdi_recipient_code == SDI_PEC_CODE:
        transmission["PECDestinatario"] = ident.pec
    return transmission


def mask_tax_code(tax_code: str) -> str:
    """Last four characters only: for audit rows and screens (SEC.3)."""
    if len(tax_code) <= 4:
        return "*" * len(tax_code)
    return "*" * (len(tax_code) - 4) + tax_code[-4:]


def mask_iban(iban: str) -> str:
    """Country code and last four characters only: for audit rows and screens."""
    code = compact(iban)
    if len(code) <= 6:
        return "*" * len(code)
    return code[:2] + "*" * (len(code) - 6) + code[-4:]


# --- Onboarding (VEN.5): contract acceptance, checklist and activation gate
#
# Only the acceptance of the contract in force blocks activation. The DAC7 dataset and the payout
# data are listed so staff and vendors see what is still missing, but they never block a status
# change: completeness gates payouts (`assert_payout_allowed`, VEN.12).

CONTRACT: Final = "contract"
DAC7: Final = "dac7"
PAYOUT_DATA: Final = "payout_data"

# Statuses whose owners may accept the contract: approved vendors, also to accept a new version.
ACCEPTING_STATUSES: Final = frozenset({ONBOARDING, ACTIVE, SUSPENDED})


class ContractError(ValueError):
    """A vendor contract operation that the rules refuse."""


class NoContract(ContractError):
    """No contract version (with a published text) is in force."""


class ContractNotAcceptable(ContractError):
    """This user cannot accept the contract for this vendor (status or role)."""


class ContractLocked(ContractError):
    """The contract version has been accepted: it can no longer change. Publish a new version."""


class ContractChanged(ContractError):
    """The version or text the owner was shown is no longer the one to accept: show it again."""


def check_can_accept_contract(status: str, role: str | None) -> None:
    """An owner of an approved vendor accepts the contract on the vendor's behalf."""
    if status not in ACCEPTING_STATUSES:
        raise ContractNotAcceptable(f"a vendor in status {status} cannot accept the contract")
    if role != OWNER:
        raise ContractNotAcceptable("only an owner of the vendor can accept the contract")


def contract_start_error(valid_from: Any, latest_valid_from: Any | None) -> str | None:
    """Why a new contract version cannot start at `valid_from`, or None: versions start in order."""
    if latest_valid_from is not None and valid_from <= latest_valid_from:
        return f"Must be after {latest_valid_from.isoformat()}, the start of the latest version."
    return None


@dataclasses.dataclass(frozen=True)
class ChecklistItem:
    """One onboarding step: done or not, whether it blocks activation, and what is missing."""

    key: str
    done: bool
    blocking: bool
    missing: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class OnboardingChecklist:
    items: tuple[ChecklistItem, ...]

    @property
    def blocking_missing(self) -> tuple[str, ...]:
        """The keys of the blocking steps not done yet."""
        return tuple(item.key for item in self.items if item.blocking and not item.done)

    @property
    def ready(self) -> bool:
        """Every blocking step is done: the vendor can be activated."""
        return not self.blocking_missing


def onboarding_checklist(
    *,
    contract_version: int | None,
    current_contract_version: int | None,
    dac7_missing: Sequence[str],
    payout: Completeness,
) -> OnboardingChecklist:
    """The onboarding checklist of a vendor.

    `contract_version` is the version the vendor accepted, if it accepted the one in force
    (`current_contract_version`); the DAC7 and payout items come from `dac7_missing` and the
    payout `completeness` of the legal profile.
    """
    if current_contract_version is None:
        contract = ChecklistItem(CONTRACT, False, True, ("no contract is in force",))
    elif contract_version != current_contract_version:
        contract = ChecklistItem(CONTRACT, False, True, (f"version {current_contract_version}",))
    else:
        contract = ChecklistItem(CONTRACT, True, True)
    return OnboardingChecklist(
        items=(
            contract,
            ChecklistItem(DAC7, not dac7_missing, False, tuple(dac7_missing)),
            ChecklistItem(PAYOUT_DATA, payout.complete, False, payout.missing + payout.invalid),
        )
    )


class ActivationNotAllowed(InvalidTransition):
    """The vendor cannot be activated yet: `blocking` names the onboarding steps not done."""

    def __init__(self, checklist: OnboardingChecklist) -> None:
        self.blocking = checklist.blocking_missing
        steps = {item.key: item for item in checklist.items}
        detail = "; ".join(
            f"{key} ({', '.join(steps[key].missing)})" if steps[key].missing else key
            for key in self.blocking
        )
        super().__init__(f"the vendor cannot be activated until onboarding is done: {detail}")


def check_activation(checklist: OnboardingChecklist) -> None:
    if not checklist.ready:
        raise ActivationNotAllowed(checklist)
