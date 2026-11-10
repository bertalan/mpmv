"""Pure vendor rules (no ORM): status state machine, slug generation, member roles and their
permissions, and the legal and e-invoicing identity (validators, required fields, FatturaPA
party block, RF20 eligibility), onboarding and the establishment gate (CMP.13)."""

import dataclasses
import datetime as dt
import hashlib
import hmac
import re
from collections.abc import Collection, Container, Iterable, Mapping, Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from apps.core.countries import EU_MEMBER_STATES as EU_MEMBER_STATES  # TIN_FORMATS covers them
from apps.core.countries import ISO_3166_ALPHA2, is_eu
from apps.core.text import slugify

# --- Status state machine (spec 04 §4.2)

APPLICANT: Final = "applicant"
ONBOARDING: Final = "onboarding"
ACTIVE: Final = "active"
SUSPENDED: Final = "suspended"
CLOSED: Final = "closed"

STATUSES: Final = (APPLICANT, ONBOARDING, ACTIVE, SUSPENDED, CLOSED)

# Forward along the lifecycle, reinstatement after a suspension, and closure from any open
# status (a rejected application is closed too). A closed vendor is only reopened by staff, back
# to onboarding and never straight to active (VEN.21, owner decision Q-17).
TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    APPLICANT: frozenset({ONBOARDING, CLOSED}),
    ONBOARDING: frozenset({ACTIVE, CLOSED}),
    ACTIVE: frozenset({SUSPENDED, CLOSED}),
    SUSPENDED: frozenset({ACTIVE, CLOSED}),
    CLOSED: frozenset({ONBOARDING}),
}

# Transitions that need a reason, recorded in the audit log: the reopening of a closed vendor
# (`services.reopen_vendor`). `services.change_status` passes no reason, so it cannot make them.
REASON_REQUIRED: Final[frozenset[tuple[str, str]]] = frozenset({(CLOSED, ONBOARDING)})


class InvalidTransition(ValueError):
    """Raised when a vendor status change is not allowed by the state machine."""


class ReasonRequired(InvalidTransition):
    """The status change needs a reason (`REASON_REQUIRED`) and none was given."""


class ReopenNotAllowed(InvalidTransition):
    """The closed vendor cannot be reopened as it is: no active owner could onboard it."""


def allowed_transitions(status: str) -> frozenset[str]:
    if status not in TRANSITIONS:
        raise InvalidTransition(f"unknown vendor status {status!r}")
    return TRANSITIONS[status]


def check_transition(current: str, target: str, *, reason: str = "") -> None:
    if target not in TRANSITIONS:
        raise InvalidTransition(f"unknown vendor status {target!r}")
    if target not in allowed_transitions(current):
        raise InvalidTransition(f"vendor status change {current} → {target} is not allowed")
    if (current, target) in REASON_REQUIRED and not reason.strip():
        raise ReasonRequired(
            f"vendor status change {current} → {target} needs a reason: "
            "staff reopen a closed vendor from its page, saying why"
        )


def check_reopen(status: str, *, reason: str, has_active_owner: bool) -> None:
    """Only a closed vendor is reopened, with a reason, back to onboarding (VEN.21).

    Onboarding needs an owner to accept the contract: without an active owner (e.g. the only
    owner asked for erasure, Q-16) a new owner is added first.
    """
    # TODO(CMP.4): once erasure flags restricted owners, exclude them here instead of relying
    # on `is_active` alone, so a reactivated erased account cannot reopen the vendor.
    if status != CLOSED:
        raise InvalidTransition(
            f"vendor status change {status} → {ONBOARDING} is not a reopen: "
            "only a closed vendor is reopened"
        )
    check_transition(status, ONBOARDING, reason=reason)
    if not has_active_owner:
        raise ReopenNotAllowed(
            "the vendor has no active owner to onboard it again: add a new owner first"
        )


class VendorCannotWrite(Exception):
    """Raised when a write on behalf of a vendor's API key finds the vendor not active (CORE.14).

    `status` is the vendor's stored status at the time of the write. Not a Django
    `PermissionDenied`: engines import no Django (ARC.18); the API maps it to its refusal.
    """

    def __init__(self, status: str) -> None:
        super().__init__(f"the vendor is {status}: only an active vendor's API keys write")
        self.status = status


def check_can_write(status: str) -> None:
    """Raise VendorCannotWrite unless a vendor with `status` may write through its API keys."""
    if status != ACTIVE:
        raise VendorCannotWrite(status)


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
    EVENTS = "events"  # the vendor's event pages: drafts, submit, cancel (spec 15, EVT.3)


# The role matrix. Owner: everything about their own vendor, financial settings included.
# Staff: day-to-day operations only; no payouts, IBAN, tax regime, profile or members.
ROLE_PERMISSIONS: Final[Mapping[str, frozenset[VendorAction]]] = {
    OWNER: frozenset(VendorAction),
    STAFF: frozenset(
        {VendorAction.ORDERS, VendorAction.QUOTES, VendorAction.PRODUCTS, VendorAction.EVENTS}
    ),
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

# The Agenzia delle Entrate FatturaPA technical specification (Specifiche tecniche) that the
# regime list and the party blocks follow (schema FPR12 1.2.x). Version 1.9 added RF20 from
# 2025-04-01; a new version updates this constant and the pinned list in the tests (VEN.18).
FATTURAPA_SPECIFICATION_VERSION: Final = "1.9.1"

# FatturaPA `RegimeFiscale`, with the official labels of the SDI specification.
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
    "RF20": "Regime transfrontaliero di Franchigia IVA (Direttiva UE 2020/285)",
}
FLAT_RATE_REGIME: Final = "RF19"  # forfettario
CROSS_BORDER_FRANCHISE_REGIME: Final = "RF20"  # Directive (EU) 2020/285, per member state

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

# TIN formats per issuing Member State (DAC7, owner decision MQ-6, VEN.23), after the structures
# the Member States publish on the European Commission's "TIN on the web" for individuals and
# entities. Checked on the TIN without separators (dots, slashes, hyphens). The Italian TIN is
# the codice fiscale, checked with its check character instead.
TIN_FORMATS: Final[Mapping[str, re.Pattern[str]]] = {
    country: re.compile(pattern, re.ASCII)
    for country, pattern in {
        "AT": r"\d{9}",
        "BE": r"\d{10,11}",
        "BG": r"\d{9,10}",
        "CY": r"\d{8}[A-Z]",
        "CZ": r"\d{8,10}",
        "DE": r"\d{10,13}",
        "DK": r"\d{8}|\d{10}",
        "EE": r"\d{8}|\d{11}",
        "ES": r"[0-9A-Z]\d{7}[0-9A-Z]",
        "FI": r"\d{6}[-+A-FUVWXY]?\d{3}[0-9A-Y]|\d{8}",
        "FR": r"\d{9}|[0-3]\d{12}",
        "GR": r"\d{9}",
        "HR": r"\d{11}",
        "HU": r"\d{10,11}",
        "IE": r"\d{7}[A-W][A-IW]?",
        ITALY: r"[A-Z0-9]{11}|[A-Z0-9]{16}",  # checked as a codice fiscale (is_valid_tin)
        "LT": r"\d{9,11}",
        "LU": r"\d{11}|\d{13}",
        "LV": r"\d{11}",
        "MT": r"\d{7}[MGAPLHBZ]|\d{9}",
        "NL": r"\d{9}",
        "PL": r"\d{10,11}",
        "PT": r"\d{9}",
        "RO": r"\d{2,10}|\d{13}",
        "SE": r"\d{10}|\d{12}",
        "SI": r"\d{8}",
        "SK": r"\d{9,10}",
    }.items()
}
# Any other country: a plausible identifier (letters and digits, with the usual separators).
_ANY_TIN = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,29}", re.ASCII)
_TIN_SEPARATORS = re.compile(r"[./-]")
# Jurisdictions that issue no TIN (in their absence DAC7 asks an individual's place of birth).
# The live list is staff-editable data (`NoTinCountry`, VEN.27) that the service passes in; this
# is only the list migration 0026 seeds it with, and the default of the pure unit-test calls.
SEEDED_NO_TIN_COUNTRIES: Final = frozenset({"AE", "BM", "BS", "KY", "MC", "VG"})
# Member States where an entity's VAT number, without its prefix, is also its TIN: only there is
# "same as the VAT number" offered. Elsewhere the two differ (AT "U" + 8 digits, DE, FR, NL, SE
# "01" suffix, EE, HU, LT, LU, MT): the vendor enters the TIN. A test derives a valid TIN for
# every country listed here.
SAME_AS_VAT_COUNTRIES: Final = frozenset(
    {
        "BE",
        "BG",
        "CY",
        "CZ",
        "DK",
        "ES",
        "FI",
        "GR",
        "HR",
        "IE",
        ITALY,
        "LV",
        "PL",
        "PT",
        "RO",
        "SI",
        "SK",
    }
)

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


def is_valid_tin(tin: str, country: str) -> bool:
    """`tin` has the format of the TINs issued by `country` (`TIN_FORMATS`); a country without a
    format in the table only needs a plausible identifier. Italy: a valid codice fiscale."""
    code = compact(tin)
    if not _ANY_TIN.fullmatch(code):
        return False
    if country == ITALY:
        return is_valid_codice_fiscale(code)
    pattern = TIN_FORMATS.get(country)
    return pattern is None or bool(pattern.fullmatch(_TIN_SEPARATORS.sub("", code)))


# --- Cross-border SME franchise number (VEN.25, owner decision MQ-5)
#
# A small enterprise using the VAT franchise of another member state (art. 284(2) of the VAT
# directive as amended by Directive (EU) 2020/285) gets from its member state of establishment
# an individual identification number with the suffix "EX" (art. 284(3)). The national part
# follows the issuing member state's VAT number format, after its VAT prefix (EL for Greece).

FRANCHISE_NUMBER_SUFFIX: Final = "EX"

# National part of the number, by issuing member state (ISO code): the VIES VAT number formats.
CROSS_BORDER_FRANCHISE_NUMBER_FORMATS: Final[Mapping[str, str]] = {
    "AT": r"U\d{8}",
    "BE": r"[01]\d{9}",
    "BG": r"\d{9,10}",
    "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}",
    "DE": r"\d{9}",
    "DK": r"\d{8}",
    "EE": r"\d{9}",
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}",
    "FR": r"[A-HJ-NP-Z0-9]{2}\d{9}",
    "GR": r"\d{9}",
    "HR": r"\d{11}",
    "HU": r"\d{8}",
    "IE": r"\d{7}[A-W][A-I]?|\d[A-Z+*]\d{5}[A-W]",
    "IT": r"\d{11}",  # country-literal: the Italian issuer format, one EU member state of 27
    "LT": r"\d{9}|\d{12}",
    "LU": r"\d{8}",
    "LV": r"\d{11}",
    "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}",
    "PL": r"\d{10}",
    "PT": r"\d{9}",
    "RO": r"[1-9]\d{1,9}",
    "SE": r"\d{12}",
    "SI": r"\d{8}",
    "SK": r"\d{10}",
}
_FRANCHISE_NUMBER_ISSUERS: Final = {
    vat_prefix(country): (country, re.compile(pattern, re.ASCII))
    for country, pattern in CROSS_BORDER_FRANCHISE_NUMBER_FORMATS.items()
}


def franchise_number_issuer(number: str) -> str | None:
    """The member state (ISO code) that issued a well-formed `EX` number; None otherwise."""
    code = compact(number)
    if not code.endswith(FRANCHISE_NUMBER_SUFFIX):
        return None
    issuer = _FRANCHISE_NUMBER_ISSUERS.get(code[:2])
    if issuer is None:
        return None
    country, national = issuer
    return country if national.fullmatch(code[2 : -len(FRANCHISE_NUMBER_SUFFIX)]) else None


@dataclasses.dataclass(frozen=True)
class LegalIdentity:
    """A vendor's legal and e-invoicing identity, as plain values (field names = model fields)."""

    country: str
    legal_name: str = ""
    legal_form: str = ""
    first_name: str = ""
    last_name: str = ""
    date_of_birth: dt.date | None = None
    place_of_birth: str = ""
    vat_number: str = ""
    tax_code: str = ""
    tin_country: str = ""
    tax_residence_country: str = ""
    fiscal_regime: str = ""
    cross_border_franchise_number: str = ""
    ateco_code: str = ""
    eori: str = ""
    business_registration_number: str = ""
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
    account_holder_name: str = ""
    contact_email: str = ""
    phone: str = ""
    # Not fields a requirement can name: an option and the VIES state of the VAT number.
    tin_same_as_vat: bool = False
    vies_status: str = VIES_UNCHECKED

    def values(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# Fields a `LegalFieldRequirement` may name. Country, legal name and contact email are always
# present on a vendor; the phone is not a legal field.
LEGAL_FIELDS: Final = tuple(
    f.name
    for f in dataclasses.fields(LegalIdentity)
    if f.name
    not in {"country", "legal_name", "contact_email", "phone", "tin_same_as_vat", "vies_status"}
)

# Identifiers stored compact (upper case, no spaces).
COMPACT_FIELDS: Final = (
    "vat_number",
    "tax_code",
    "eori",
    "rea_number",
    "sdi_recipient_code",
    "fiscal_representative_vat_number",
    "cross_border_franchise_number",
    "iban",
    "bic",
)

FRANCHISE_NUMBER_FIELD: Final = "cross_border_franchise_number"


def _address_country(identity: LegalIdentity, prefix: str) -> str:
    country: str = getattr(identity, f"{prefix}_country")
    return country or (identity.country if prefix == "registered" else "")


def rf20_seat_country(identity: LegalIdentity) -> str:
    """The member state where the vendor's registered office (seat) is, as the CMP.13
    `vendor_establishment` reads it; "" when the seat is outside the EU.

    Owner interim decision MQ-17: until the tax advisor answers, a permanent establishment alone
    does not qualify for RF20, so only the seat counts. Revisit when MQ-17 is answered.
    """
    seat = vendor_establishment(identity).registered_office
    return seat if seat in EU_MEMBER_STATES else ""


def cross_border_franchise_errors(identity: LegalIdentity) -> dict[str, list[str]]:
    """Who may declare RF20 (VEN.25, owner decision MQ-5), by field.

    A vendor declaring RF20 must have its registered office (seat) in an EU member state and hold
    the `EX` number (art. 284(3) of the VAT directive). A number, when given, must be well formed
    for its issuing member state and issued by the seat's member state. A permanent
    establishment alone does not qualify (owner interim decision MQ-17).
    """
    errors: dict[str, list[str]] = {}
    number = compact(identity.cross_border_franchise_number)
    seat = rf20_seat_country(identity)
    if number:
        issuer = franchise_number_issuer(number)
        if issuer is None:
            errors[FRANCHISE_NUMBER_FIELD] = [
                "Not a valid cross-border franchise number: the issuing member state's VAT "
                "prefix, its national number and the suffix EX (e.g. DE123456789EX)."
            ]
        elif issuer != seat:
            where = seat or "no EU member state"
            errors[FRANCHISE_NUMBER_FIELD] = [
                f"The number was issued by {issuer}, but the vendor's registered office is in "
                f"{where}: it must be issued by the member state of the registered office."
            ]
    if identity.fiscal_regime == CROSS_BORDER_FRANCHISE_REGIME:
        if not seat:
            errors["fiscal_regime"] = [
                "RF20 (cross-border VAT franchise) is only for vendors whose registered office "
                "is in an EU member state; a permanent establishment alone does not qualify."
            ]
        if not number:
            errors[FRANCHISE_NUMBER_FIELD] = [
                "RF20 needs the cross-border franchise number (suffix EX) issued by the member "
                "state of the registered office."
            ]
    return errors


def normalized(identity: LegalIdentity) -> LegalIdentity:
    """Identifiers compacted; Italian province codes in upper case."""
    changes: dict[str, Any] = {name: compact(getattr(identity, name)) for name in COMPACT_FIELDS}
    for prefix in ("registered", "establishment"):
        province = getattr(identity, f"{prefix}_province")
        if _address_country(identity, prefix) == ITALY:
            changes[f"{prefix}_province"] = compact(province)
    changes["pec"] = identity.pec.strip()
    changes["place_of_birth"] = identity.place_of_birth.strip()
    changes["business_registration_number"] = identity.business_registration_number.strip()
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
    else:
        _, number = split_vat_number(ident.vat_number, ident.country)
        check("vat_number", bool(_VAT_CODE.fullmatch(number)), "Not a valid VAT number.")
    tin_country = ident.tin_country or ident.country
    check(
        "tax_code",
        is_valid_tin(ident.tax_code, tin_country),
        "Not a valid codice fiscale (16 characters, or 11 digits for entities)."
        if tin_country == ITALY
        else f"Not a valid TIN for {tin_country}.",
    )
    if ident.tin_same_as_vat:
        if ident.legal_form == SOLE_PROPRIETORSHIP:
            errors["tin_same_as_vat"] = ["Only an entity's TIN can be its VAT number."]
        elif not ident.vat_number:
            errors["tin_same_as_vat"] = ["There is no VAT number to use as the TIN."]
        elif _vat_number_as_tin(ident) is None:
            errors["tin_same_as_vat"] = [
                f"In {ident.country or 'this country'} the VAT number is not the TIN: "
                "enter the TIN instead."
            ]
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
    for name, messages in cross_border_franchise_errors(ident).items():
        errors.setdefault(name, []).extend(messages)
    return errors


# --- Required legal fields (rules are data: `LegalFieldRequirement`)

PAYOUT: Final = "payout"
ACTIVATION: Final = "activation"  # CMP.25: the DSA article 30 trader data, checked at activation
PURPOSES: Final = (PAYOUT, ACTIVATION)

# What a requirement can name: the legal fields and the phone (a DSA article 30 datum, not a
# legal field of the identity).
REQUIREMENT_FIELDS: Final = (*LEGAL_FIELDS, "phone")

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
    """The fields the rules require from this vendor, in `REQUIREMENT_FIELDS` order.

    Rules are additive: every rule matching the vendor's country and legal form applies (there
    is no "most specific wins"); staff relax a requirement by deactivating its rule. While the
    legal form is unknown, a rule for a specific form cannot be decided, so the legal form
    itself becomes required.
    """
    country = str(values.get("country") or "")
    legal_form = str(values.get("legal_form") or "")
    required: set[str] = set()
    for rule in rules:
        if rule.field not in REQUIREMENT_FIELDS:
            raise UnknownLegalField(f"{rule.field!r} is not a legal field")
        if rule.purpose != purpose or rule.country not in ("", country):
            continue
        if rule.region and (not country or rule.region != region_of(country)):
            continue
        if rule.legal_form and not legal_form:
            required.add("legal_form")
        elif rule.legal_form in ("", legal_form):
            required.add(rule.field)
    return [name for name in REQUIREMENT_FIELDS if name in required]


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


# --- DAC7 seller dataset (owner decision MQ-6, VEN.23; spec 11 CMP.1)
#
# Directive (EU) 2021/514, Annex V, section III.B.2: for each reportable seller, (a) the data
# collected under section II.B.1 (individuals) or II.B.2 (entities), then (b) the financial
# account identifier, (c) the account holder's name where it differs from the seller's and
# (d) the Member State of residence. (e) and (f), the consideration, activities and fees per
# quarter, come from the ledger (CMP.1), not from the vendor. A test pins both lists.

_PRIMARY_ADDRESS: Final = (
    "registered_street",
    "registered_street_number",
    "registered_postal_code",
    "registered_city",
    "registered_province",
    "registered_country",
)
_ACCOUNT_AND_RESIDENCE: Final = (
    ("III.B.2(b)", "financial account identifier", ("iban",)),
    (
        "III.B.2(c)",
        "name of the financial account holder, where different",
        ("account_holder_name",),
    ),
    ("III.B.2(d)", "Member State of residence", ("tax_residence_country",)),
)
DAC7_INDIVIDUAL_DATASET: Final = (
    ("II.B.1(a)", "first and last name", ("first_name", "last_name")),
    ("II.B.1(b)", "primary address", _PRIMARY_ADDRESS),
    (
        "II.B.1(c)",
        "TIN with its Member State of issuance, or the place of birth without a TIN",
        ("tax_code", "tin_country", "place_of_birth"),
    ),
    ("II.B.1(d)", "VAT identification number, where available", ("vat_number",)),
    ("II.B.1(e)", "date of birth", ("date_of_birth",)),
    *_ACCOUNT_AND_RESIDENCE,
)
DAC7_ENTITY_DATASET: Final = (
    ("II.B.2(a)", "legal name", ("legal_name",)),
    ("II.B.2(b)", "primary address", _PRIMARY_ADDRESS),
    ("II.B.2(c)", "TIN with its Member State of issuance", ("tax_code", "tin_country")),
    ("II.B.2(d)", "VAT identification number, where available", ("vat_number",)),
    ("II.B.2(e)", "business registration number", ("business_registration_number",)),
    (
        "II.B.2(f)",
        "permanent establishment in the Union, where available",
        tuple(name.replace("registered", "establishment") for name in _PRIMARY_ADDRESS),
    ),
    *_ACCOUNT_AND_RESIDENCE,
)
# Every vendor field that is part of the dataset, or decides which part applies: staff change
# them only behind an MFA step-up (`services.correct_dac7_dataset`).
DAC7_FIELDS: Final = frozenset(
    {
        "legal_form",
        "tin_same_as_vat",
        *(name for _, _, names in DAC7_INDIVIDUAL_DATASET + DAC7_ENTITY_DATASET for name in names),
    }
)
# Order of the field names in a `Completeness`.
_FIELD_ORDER: Final = ("legal_name", *LEGAL_FIELDS)


def _in_field_order(names: Iterable[str]) -> tuple[str, ...]:
    wanted = set(names)
    return tuple(name for name in _FIELD_ORDER if name in wanted)


def is_individual(identity: LegalIdentity) -> bool:
    """A sole proprietor is an individual seller for DAC7; every other legal form an entity."""
    return identity.legal_form == SOLE_PROPRIETORSHIP


def tin_from_vat(identity: LegalIdentity) -> LegalIdentity:
    """Apply "same as the VAT number": the TIN is the VAT number (without its prefix) issued by
    the vendor's country once VIES says it is valid, and nothing until then. Without the option
    the identity is returned unchanged.

    Only in `SAME_AS_VAT_COUNTRIES`, and only when the derived value is a valid TIN there:
    otherwise no TIN is derived (and `identity_errors` refuses the option). While VIES is
    unavailable, a TIN already derived from this VAT number is kept: only a definite answer
    changes it.
    """
    if not identity.tin_same_as_vat:
        return identity
    derived = _vat_number_as_tin(identity)
    if derived is None:
        return dataclasses.replace(identity, tax_code="", tin_country="")
    country, number = derived
    if identity.vies_status == VIES_VALID or (
        identity.vies_status == VIES_UNAVAILABLE
        and (compact(identity.tax_code), identity.tin_country) == (number, country)
    ):
        return dataclasses.replace(identity, tax_code=number, tin_country=country)
    return dataclasses.replace(identity, tax_code="", tin_country="")


def _vat_number_as_tin(identity: LegalIdentity) -> tuple[str, str] | None:
    """(country, TIN) taken from the VAT number, where that number is a valid TIN; else None."""
    if not identity.vat_number or identity.country not in SAME_AS_VAT_COUNTRIES:
        return None
    country, number = split_vat_number(identity.vat_number, identity.country)
    return (country, number) if is_valid_tin(number, country) else None


def dac7_check(
    identity: LegalIdentity, no_tin_countries: Collection[str] = SEEDED_NO_TIN_COUNTRIES
) -> Completeness:
    """The DAC7 dataset of `identity`: fields that are missing, and present ones that are
    invalid (a TIN malformed for its country, a VAT number VIES found invalid, ...).

    The parts depend on the legal form: names and date of birth for an individual, legal name
    and business registration number for an entity. The TIN is always needed with its issuing
    country; when that country issues no TIN, an individual gives the place of birth instead.
    An entity always needs a TIN (or the identifier its country uses as one), so every entity
    that passes has a tax identifier for FatturaPA.
    The VAT number, the account holder's name and a permanent establishment are reported only
    when there is one, so they are never missing.

    `no_tin_countries` is the staff-editable list of jurisdictions that issue no TIN (VEN.27).
    """
    ident = normalized(identity)
    missing: list[str] = []

    def need(*names: str) -> None:
        missing.extend(name for name in names if not is_filled(name, ident.values()))

    if not ident.legal_form:
        need("legal_form")
    if is_individual(ident):
        need("first_name", "last_name", "date_of_birth")
    else:
        need("legal_name")
        if ident.legal_form:
            need("business_registration_number")
    need("registered_street", "registered_postal_code", "registered_city", "registered_country")
    if ident.registered_country == ITALY:
        need("registered_province")
    need("tin_country")
    if ident.tin_country in no_tin_countries and is_individual(ident):
        if not ident.tax_code:
            need("place_of_birth")
    else:
        need("tax_code")  # an entity always: the place of birth is no fallback for it
    need("tax_residence_country", "iban")

    errors = identity_errors(ident)
    invalid = {name for name in DAC7_FIELDS if name in errors and name not in missing}
    if ident.vat_number and ident.vies_status == VIES_INVALID:
        invalid.add("vat_number")
    return Completeness(missing=_in_field_order(missing), invalid=_in_field_order(invalid))


def dac7_missing(
    identity: LegalIdentity, no_tin_countries: Collection[str] = SEEDED_NO_TIN_COUNTRIES
) -> tuple[str, ...]:
    """The DAC7 fields still missing or invalid, in field order (see `dac7_check`)."""
    result = dac7_check(identity, no_tin_countries)
    return _in_field_order((*result.missing, *result.invalid))


def payout_completeness(
    identity: LegalIdentity,
    rules: Iterable[Requirement],
    no_tin_countries: Collection[str] = SEEDED_NO_TIN_COUNTRIES,
) -> Completeness:
    """What a payout still needs: the staff-editable rules plus the DAC7 dataset.

    The DAC7 check is not a rule: deactivating requirement rows relaxes only what the rows add,
    never the dataset the platform must report.
    """
    by_rules = completeness(identity, rules, purpose=PAYOUT)
    dac7 = dac7_check(identity, no_tin_countries)
    missing = _in_field_order((*by_rules.missing, *dac7.missing))
    invalid = _in_field_order(
        name for name in (*by_rules.invalid, *dac7.invalid) if name not in missing
    )
    return Completeness(missing=missing, invalid=invalid)


class StepUpRequired(PermissionError):
    """Staff change the DAC7 dataset only right after a second-factor verification (CORE.4)."""


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


# --- FatturaPA 1.2.x party blocks, specification `FATTURAPA_SPECIFICATION_VERSION` (data only:
# CMP.2 writes the XML)

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

    Keys follow the FatturaPA element names and order of technical specification
    `FATTURAPA_SPECIFICATION_VERSION`. Raises `PartyBlockError` listing every
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
            block.problems.append("fiscal_regime: RegimeFiscale is mandatory (RF01-RF20)")
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
    """The last characters only, at most four and at most a third of the code, so a short TIN
    is not half readable: for audit rows and screens (SEC.3)."""
    shown = min(4, len(tax_code) // 3)
    return "*" * (len(tax_code) - shown) + tax_code[len(tax_code) - shown :]


def mask_personal(value: object) -> str:
    """Personal data (date and place of birth): only whether a value is there."""
    return "" if value is None or value == "" else "********"


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
PLATFORM_DUTIES: Final = "platform_duties"  # CMP.25
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
    current_contract_version: int | None = None  # the version in force, if any

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
    platform_duties_missing: Sequence[str] = (),
) -> OnboardingChecklist:
    """The onboarding checklist of a vendor.

    `contract_version` is the version the vendor accepted, if it accepted the one in force
    (`current_contract_version`); the DAC7 and payout items come from `dac7_missing` and the
    payout `completeness` of the legal profile. `platform_duties_missing` (CMP.25, see
    `platform_duties_missing`) blocks activation; DAC7 and payout data never do.
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
            ChecklistItem(
                PLATFORM_DUTIES, not platform_duties_missing, True, tuple(platform_duties_missing)
            ),
            ChecklistItem(DAC7, not dac7_missing, False, tuple(dac7_missing)),
            ChecklistItem(PAYOUT_DATA, payout.complete, False, payout.missing + payout.invalid),
        ),
        current_contract_version=current_contract_version,
    )


# --- Platform duties at activation (CMP.25)
#
# DSA article 30: before a vendor can sell, the platform holds its name, address, telephone and
# email and its trade register entry where registered (the `activation` requirement rows), and
# article 30(1)(e): the vendor's self-certification that it offers only products that comply
# with Union law. Unlike a self-attestation (CMP.10) it blocks activation. DAC7 data does not.

SELF_CERTIFICATION: Final = "self_certification"
# Bump when a text changes: acceptances keep the version and hash they were given.
# The wording is a placeholder until the one-off legal review (CMP.24).
SELF_CERTIFICATION_VERSION: Final = 1
SELF_CERTIFICATION_TEXTS: Final[Mapping[str, str]] = {
    "en": (
        "I certify, on behalf of the vendor, that it will offer on this marketplace only "
        "products and services that comply with the applicable rules of Union law."
    ),
    "it": (
        "Certifico, a nome del venditore, che offrirà su questo marketplace solo prodotti e "
        "servizi conformi alle norme applicabili del diritto dell'Unione."
    ),
}


class SelfCertificationError(ValueError):
    """The self-certification cannot be recorded (the message is safe to show)."""


def self_certification_text(language: str) -> str:
    try:
        return SELF_CERTIFICATION_TEXTS[language]
    except KeyError:
        raise SelfCertificationError(
            f"there is no self-certification text in {language!r}"
        ) from None


def self_certification_hash(language: str) -> str:
    """SHA-256 of the version, the language code and the text, as stored with the acceptance."""
    text = self_certification_text(language)
    return hashlib.sha256(f"{SELF_CERTIFICATION_VERSION}\n{language}\n{text}".encode()).hexdigest()


def check_can_certify(status: str, role: str | None) -> None:
    """An owner of a vendor still in onboarding certifies on the vendor's behalf."""
    if status not in ACCEPTING_STATUSES:
        raise SelfCertificationError(f"a vendor in status {status} cannot certify")
    if role != OWNER:
        raise SelfCertificationError("only an owner of the vendor can certify")


def platform_duties_missing(
    values: Mapping[str, Any], rules: Iterable[Requirement], *, certified: bool
) -> tuple[str, ...]:
    """What activation still needs: the `activation` fields the rules require and the vendor has
    not filled in (in field order), then `SELF_CERTIFICATION` unless `certified`. Pure."""
    missing = missing_required_fields(values, rules, purpose=ACTIVATION)
    return (*missing, *(() if certified else (SELF_CERTIFICATION,)))


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


# --- Claiming an imported vendor (VEN.24, owner decision MQ-1, spec 04 "Flows")
#
# A vendor imported without an account holder is claimed through an email-bound secure link
# (CORE.15) sent to its legal email of record. The claimant proves they know the stored VAT
# number or TIN; staff approve the claim before the claimant becomes the owner.

CLAIM_OPEN: Final = "open"  # a link is out, waiting for the claimant
CLAIM_LOCKED: Final = "locked"  # too many wrong VAT numbers or TINs: staff regenerate the link
CLAIM_SUBMITTED: Final = "submitted"  # account created, waiting for staff approval
CLAIM_APPROVED: Final = "approved"  # the claimant is the vendor's owner
CLAIM_REJECTED: Final = "rejected"  # staff refused it: nothing granted, a new link may follow


class ClaimError(ValueError):
    """A claim step the rules refuse (the message is safe to show to staff)."""


# Where the link goes (decision on PR #141): the legal email of record, or, when the vendor
# has one on file, the PEC address staff pick instead. No other address can be chosen.
CLAIM_CHANNEL_EMAIL: Final = "email"
CLAIM_CHANNEL_PEC: Final = "pec"


def check_claim_link_can_be_sent(status: str | None, *, by_staff: bool) -> None:
    """A link is sent for a new claim, or regenerated while no claim waits for staff and none
    was approved (a pending claim is decided first). After a reject only staff start a new
    claim (decision on PR #141): nobody else, the claimant included, re-triggers it."""
    if status == CLAIM_SUBMITTED:
        raise ClaimError("a claim is waiting for approval: approve or reject it first")
    if status == CLAIM_APPROVED:
        raise ClaimError("the vendor was already claimed")
    if status == CLAIM_REJECTED and not by_staff:
        raise ClaimError("the claim was rejected: only staff can start a new one")


def claim_channels(*, contact_email: str, pec: str) -> tuple[str, ...]:
    """The destinations staff may pick: the legal email of record, and the PEC when on file."""
    channels: tuple[str, ...] = (CLAIM_CHANNEL_EMAIL,) if contact_email.strip() else ()
    return (*channels, CLAIM_CHANNEL_PEC) if pec.strip() else channels


def check_claim_channel(channel: str, *, contact_email: str, pec: str) -> None:
    """Refuse a destination other than the legal email of record or the PEC on file."""
    if channel not in claim_channels(contact_email=contact_email, pec=pec):
        if channel == CLAIM_CHANNEL_PEC:
            raise ClaimError("the vendor has no PEC address on file")
        if channel == CLAIM_CHANNEL_EMAIL:
            raise ClaimError("the vendor has no legal email of record")
        raise ClaimError("the link goes to the legal email of record or the PEC address only")


def claim_address(channel: str, *, contact_email: str, pec: str) -> str:
    """The address of `channel`: the PEC for the PEC channel, else the legal email."""
    return (pec if channel == CLAIM_CHANNEL_PEC else contact_email).strip()


def mask_email(address: str) -> str:
    """The first character of the local part and the domain only: for audit rows (SEC.3)."""
    local, at, domain = address.partition("@")
    if not at:
        return "*" * len(address)
    shown = local[:1] if len(local) > 1 else ""
    return shown + "*" * (len(local) - len(shown)) + "@" + domain


def claim_locks_after(failed_attempts: int, *, max_failures: int) -> bool:
    """Whether a claim with `failed_attempts` wrong answers is locked."""
    return failed_attempts >= max_failures


def _tax_id(value: str) -> str:
    """Letters and digits only, upper case: how a typed VAT number or TIN is compared."""
    return "".join(char for char in value if char.isalnum()).upper()


def _tax_id_forms(value: str, prefix: str) -> set[str]:
    """A stored identifier with and without the country's VAT prefix."""
    compacted = _tax_id(value)
    if not compacted:
        return set()
    forms = {compacted}
    if compacted.startswith(prefix) and len(compacted) > len(prefix):
        forms.add(compacted[len(prefix) :])
    else:
        forms.add(prefix + compacted)
    return forms


def claim_identity_matches(typed: str, *, vat_number: str, tax_code: str, country: str) -> bool:
    """Whether `typed` is the vendor's stored VAT number or TIN.

    Spaces, dots and dashes are ignored, letters compared in upper case, and a VAT number may
    be typed with or without the country prefix. Every stored form is compared in constant time.
    """
    candidate = _tax_id(typed)
    prefix = vat_prefix(country)
    if not candidate or candidate == prefix:
        return False
    stored = _tax_id_forms(vat_number, prefix) | _tax_id_forms(tax_code, prefix)
    matched = False
    for form in sorted(stored):
        matched |= hmac.compare_digest(candidate.encode(), form.encode())
    return matched


# --- Establishment gate (D-21, CMP.13)
#
# At go-live the platform admits only vendors established in the EU: a non-EU vendor selling
# goods in the EU would make the platform the deemed VAT supplier (VAT directive art. 14a). The
# allowed countries are a `PlatformOperator` setting; the activation checks the VEN.12 identity
# against it. Evidence of a permanent establishment is checked by staff.


@dataclasses.dataclass(frozen=True)
class Establishment:
    """The countries where a vendor is established ("" when it has no permanent establishment)."""

    registered_office: str
    permanent_establishment: str = ""

    @property
    def countries(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(c for c in (self.registered_office, self.permanent_establishment) if c)
        )


def vendor_establishment(identity: LegalIdentity) -> Establishment:
    """The registered office country (the vendor's country when not given) and the permanent
    establishment country of a legal identity."""
    return Establishment(
        registered_office=_address_country(identity, "registered"),
        permanent_establishment=_address_country(identity, "establishment"),
    )


def establishment_error(establishment: Establishment, allowed: Collection[str]) -> str | None:
    """Why a vendor established at `establishment` cannot be activated; None when it can, i.e.
    when its registered office or permanent establishment is in `allowed`."""
    if any(country in allowed for country in establishment.countries):
        return None
    office = establishment.registered_office
    if establishment.permanent_establishment:
        return (
            f"the vendor cannot be activated: its registered office is in {office} and its "
            f"permanent establishment in {establishment.permanent_establishment}, and the "
            "platform does not admit vendors established in either country"
        )
    return (
        f"the vendor cannot be activated: its registered office is in {office}, where the "
        "platform does not admit vendors, and it has no permanent establishment in a country "
        "where it does"
    )


class EstablishmentNotAllowed(InvalidTransition):
    """The vendor is not established in a country where the platform admits vendors."""

    def __init__(self, establishment: Establishment, message: str) -> None:
        self.establishment = establishment
        super().__init__(message)


def check_establishment(establishment: Establishment, allowed: Collection[str]) -> None:
    error = establishment_error(establishment, allowed)
    if error is not None:
        raise EstablishmentNotAllowed(establishment, error)
