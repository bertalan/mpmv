"""Pure compliance rules (no ORM). GPSR product data (CMP.3, spec 11).

GPSR (regulation (EU) 2023/988) article 19: an offer online shows the manufacturer's name and
postal and electronic address; when the manufacturer is not established in the EU, the name and
addresses of the responsible person in the EU (article 16); and information that identifies the
product. The product picture and the warnings in the destination language are checked by the
product page part of CMP.3.

Per-vendor defaults fill what the product leaves empty one whole block at a time (the
manufacturer block, the EU responsible person block), never field by field, so a product never
shows one manufacturer's name with another's address.

The article 22 deadlines are counted in working days, Monday to Friday. Public holidays can be
passed in; the services pass none unless `settings.COMPLIANCE_PUBLIC_HOLIDAYS` lists some, which
gives the earliest deadline (owner decision 2026-11-09).

Warnings (art. 19(c)) must be in a language of the destination country: a product with warnings
is not sold into a country until they exist in one of its languages (owner decision 2026-11-09).
"""

import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from apps.core.countries import (
    EU_MEMBER_STATES,
    OFFICIAL_LANGUAGES,
    InvalidCountry,
    normalize_country,
)

MANUFACTURER_FIELDS: Final = (
    "manufacturer_name",
    "manufacturer_address",
    "manufacturer_contact",
    "manufacturer_country",
)
EU_RESPONSIBLE_FIELDS: Final = (
    "eu_responsible_name",
    "eu_responsible_address",
    "eu_responsible_contact",
)
PARTY_FIELDS: Final = (*MANUFACTURER_FIELDS, *EU_RESPONSIBLE_FIELDS)
CONTACT_FIELDS: Final = ("manufacturer_contact", "eu_responsible_contact")

# Missing-data codes, in the order they are reported.
PRODUCT_IDENTIFIER: Final = "product_identifier"

_URL = re.compile(r"https?://[^\s\x00-\x1f\x7f/]+[^\s\x00-\x1f\x7f]*", re.IGNORECASE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_WEB = re.compile(r"^https?://[^\s/]+\.[^\s/]+\S*$", re.IGNORECASE)


@dataclass(frozen=True)
class GpsrParties:
    """Who answers for a product: the manufacturer and, when needed, the EU responsible person."""

    manufacturer_name: str = ""
    manufacturer_address: str = ""
    manufacturer_contact: str = ""
    manufacturer_country: str = ""
    eu_responsible_name: str = ""
    eu_responsible_address: str = ""
    eu_responsible_contact: str = ""


@dataclass(frozen=True)
class GpsrFacts:
    """The data a product would be published with: parties after defaults, and the values of
    its identifiers (GTIN, MPN, model number, batch), empty ones included."""

    parties: GpsrParties
    identifiers: Sequence[str] = ()


def _filled(value: str) -> bool:
    return bool(value.strip())


def _has_block(parties: GpsrParties, block: Sequence[str]) -> bool:
    return any(_filled(getattr(parties, name)) for name in block)


def merge_parties(own: GpsrParties | None, defaults: GpsrParties | None) -> GpsrParties:
    """`own` with each empty block taken whole from `defaults`."""
    if own is None:
        return defaults if defaults is not None else GpsrParties()
    if defaults is None:
        return own
    merged = own
    for block in (MANUFACTURER_FIELDS, EU_RESPONSIBLE_FIELDS):
        if not _has_block(own, block):
            merged = replace(merged, **{name: getattr(defaults, name) for name in block})
    return merged


def needs_eu_responsible(country: str) -> bool:
    """Whether a manufacturer in `country` needs an EU responsible person (GPSR art. 16).
    An empty or unknown country is not decided here: it is reported as missing."""
    if not _filled(country):
        return False
    try:
        return normalize_country(country) not in EU_MEMBER_STATES
    except InvalidCountry:
        return False


def gpsr_missing(facts: GpsrFacts) -> tuple[str, ...]:
    """Codes of the mandatory GPSR data that `facts` lacks; empty when it can be published."""
    parties = facts.parties
    missing = [name for name in MANUFACTURER_FIELDS if not _filled(getattr(parties, name))]
    if not any(_filled(value) for value in facts.identifiers):
        missing.append(PRODUCT_IDENTIFIER)
    if needs_eu_responsible(parties.manufacturer_country):
        missing.extend(
            name for name in EU_RESPONSIBLE_FIELDS if not _filled(getattr(parties, name))
        )
    return tuple(missing)


def contact_error(value: str) -> str | None:
    """Why `value` is not an electronic address (an email or an http(s) web address)."""
    value = value.strip()
    if not value or _EMAIL.match(value) or _WEB.match(value):
        return None
    return "Enter an email address or a web address starting with https://."


# --- GPSR article 22: deadlines of the marketplace's safety duties

AUTHORITY_ORDER: Final = "authority_order"
SAFETY_NOTICE: Final = "safety_notice"

#: Working days to act on an authority order (art. 22(12)) and to handle a product safety
#: notice (art. 22(9)) received through the notice channel (DSA art. 16, CMP.5).
WORKING_DAYS_TO_ACT: Final = {AUTHORITY_ORDER: 2, SAFETY_NOTICE: 3}

_SATURDAY: Final = 5


def is_working_day(day: dt.date, holidays: frozenset[dt.date] = frozenset()) -> bool:
    """Monday to Friday, except the given public holidays."""
    return day.weekday() < _SATURDAY and day not in holidays


def add_working_days(
    start: dt.date, days: int, holidays: frozenset[dt.date] = frozenset()
) -> dt.date:
    """The `days`-th working day after `start` (the day of receipt is day 0, `start` itself
    for 0 days). Without holidays the result is the earliest possible deadline."""
    if days < 0:
        raise ValueError(f"days must not be negative, got {days}")
    day = start
    for _ in range(days):
        day += dt.timedelta(days=1)
        while not is_working_day(day, holidays):
            day += dt.timedelta(days=1)
    return day


def safety_case_due_on(
    kind: str, received_on: dt.date, holidays: frozenset[dt.date] = frozenset()
) -> dt.date:
    """The last day to act on a case of `kind` received on `received_on`."""
    try:
        days = WORKING_DAYS_TO_ACT[kind]
    except KeyError:
        raise ValueError(f"unknown safety case kind {kind!r}") from None
    return add_working_days(received_on, days, holidays)


def is_overdue(due_on: dt.date, *, today: dt.date, closed: bool) -> bool:
    """An open case is overdue from the day after its due day."""
    return not closed and today > due_on


def working_days_left(
    due_on: dt.date, *, today: dt.date, holidays: frozenset[dt.date] = frozenset()
) -> int:
    """Working days from `today` to `due_on`: 0 on the due day, negative once it has passed."""
    sign, start, end = (1, today, due_on) if today <= due_on else (-1, due_on, today)
    count = 0
    day = start
    while day < end:
        day += dt.timedelta(days=1)
        if is_working_day(day, holidays):
            count += 1
    return sign * count


# --- GPSR article 19(c): warnings in a language of the destination country

NO_WARNINGS: Final = "no_warnings"
OUTSIDE_SCOPE: Final = "outside_scope"
WARNINGS_AVAILABLE: Final = "warnings_available"
WARNINGS_LANGUAGE_MISSING: Final = "warnings_language_missing"


@dataclass(frozen=True)
class WarningsSaleCheck:
    """Whether a product may be sold into `country` as far as its warnings' language goes.

    `country_languages` are the destination's languages; `available_languages` those the
    product's warnings exist in (to link to them when the sale is blocked).
    """

    allowed: bool
    reason: str
    country: str
    country_languages: tuple[str, ...] = ()
    available_languages: tuple[str, ...] = ()


def _base_language(code: str) -> str:
    return code.strip().lower().split("-")[0]


def warnings_sale_check(
    warning_languages: Iterable[str],
    destination: str,
    country_languages: Mapping[str, Sequence[str]],
) -> WarningsSaleCheck:
    """May a product whose warnings exist in `warning_languages` be sold into `destination`?

    A product without warnings may. A destination missing from `country_languages` (outside
    the EU and EEA) is not checked. Otherwise the warnings must exist in one of its languages.
    Raises ValueError (InvalidCountry) for a destination that is not a country code.
    """
    country = normalize_country(destination)
    available = tuple(sorted({code.strip().lower() for code in warning_languages}))
    if not available:
        return WarningsSaleCheck(True, NO_WARNINGS, country)
    languages = tuple(country_languages.get(country, ()))
    if not languages:
        return WarningsSaleCheck(True, OUTSIDE_SCOPE, country, (), available)
    wanted = {_base_language(code) for code in languages}
    allowed = any(_base_language(code) in wanted for code in available)
    reason = WARNINGS_AVAILABLE if allowed else WARNINGS_LANGUAGE_MISSING
    return WarningsSaleCheck(allowed, reason, country, languages, available)


def warnings_language_gaps(
    warning_languages: Iterable[str], country_languages: Mapping[str, Sequence[str]]
) -> dict[str, tuple[str, ...]]:
    """The countries a product with warnings in `warning_languages` cannot be sold into, with
    the languages its warnings would need there (one of them is enough)."""
    available = frozenset(warning_languages)
    gaps: dict[str, tuple[str, ...]] = {}
    for country in sorted(country_languages):
        check = warnings_sale_check(available, country, country_languages)
        if not check.allowed:
            gaps[country] = check.country_languages
    return gaps


# --- DSA points of contact and required legal sections (CMP.5, spec 11)

# What each regulation-driven legal page must contain: the `section` tags of the CMS
# `LegalSectionBlock`. DSA article 14 (restrictions and moderation policy in the terms) and P2B
# article 5 (ranking parameters). The wording is the editor's; code only checks the tags.
REQUIRED_SECTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    "platform_terms": ("restrictions", "moderation_policy", "complaint_handling", "redress"),
    "ranking": ("ranking_parameters",),
}
# DSA article 11(3) (owner decision D-27): the authorities can write in an official language of the
# Member State of establishment and in English.
AUTHORITY_LANGUAGE: Final = "en"


@dataclass(frozen=True)
class DsaProblem:
    """One thing that stops the DSA and P2B duties of part 1 from being met."""

    code: str
    subject: str = ""

    @property
    def message(self) -> str:
        return f"{self.code}: {self.subject}" if self.subject else self.code


def contact_problems(
    *,
    country: str,
    authority_email: str,
    authority_languages: Iterable[str],
    support_email: str,
) -> list[DsaProblem]:
    """DSA articles 11 and 12 on the operator's data: an authorities' address, the languages it
    can be used in (an official language of the Member State of establishment plus English,
    article 11(3), D-27), and an address for recipients (article 12)."""
    problems: list[DsaProblem] = []
    languages = {code.lower().split("-")[0] for code in authority_languages}
    if not authority_email:
        problems.append(DsaProblem("authority_email"))
    if not languages:
        problems.append(DsaProblem("authority_languages"))
    else:
        official = set(OFFICIAL_LANGUAGES.get(country.upper(), ()))
        if official and not official & languages:
            problems.append(DsaProblem("authority_official_language", country.upper()))
        if AUTHORITY_LANGUAGE not in languages:
            problems.append(DsaProblem("authority_english_language"))
    if not support_email:
        problems.append(DsaProblem("support_email"))
    return problems


def page_problem(kind: str, language: str) -> DsaProblem:
    return DsaProblem("page_missing", f"{kind} [{language}]: no published page")


def section_problems(kind: str, language: str, sections: Iterable[str]) -> list[DsaProblem]:
    """The required sections of `kind` that a published page in `language` does not carry."""
    present = set(sections)
    return [
        DsaProblem("section_missing", f"{kind} [{language}]: {section}")
        for section in REQUIRED_SECTIONS.get(kind, ())
        if section not in present
    ]


# --- Notice and action (DSA art. 16, CMP.5 part 2)

NOTICE_RECEIVED: Final = "received"
NOTICE_UNDER_REVIEW: Final = "under_review"
NOTICE_ACTION_TAKEN: Final = "action_taken"
NOTICE_NO_ACTION: Final = "no_action"
NOTICE_OUTCOMES: Final = (NOTICE_ACTION_TAKEN, NOTICE_NO_ACTION)
#: A notice of this category may be sent without the notifier's name and email (art. 16(2)(c)).
NOTICE_ANONYMOUS_CATEGORY: Final = "child_abuse"

_NOTICE_TRANSITIONS: Final = {
    NOTICE_RECEIVED: frozenset({NOTICE_UNDER_REVIEW}),
    NOTICE_UNDER_REVIEW: frozenset(NOTICE_OUTCOMES),
}
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class NoticeProblem:
    """One field of a notice that does not meet article 16(2)."""

    code: str


def notice_transition_allowed(current: str, new: str) -> bool:
    """received -> under_review -> action_taken or no_action; a decision is final."""
    return new in _NOTICE_TRANSITIONS.get(current, frozenset())


def notice_problems(
    *,
    explanation: str,
    url: str,
    category: str,
    notifier_name: str,
    notifier_email: str,
    good_faith: bool,
) -> list[NoticeProblem]:
    """What article 16(2) needs: an explanation, the exact location (an http or https URL), the
    notifier's name and email (not for the anonymous category) and the good-faith statement."""
    problems: list[NoticeProblem] = []
    if not explanation.strip():
        problems.append(NoticeProblem("explanation"))
    if not _URL.fullmatch(url.strip()):
        problems.append(NoticeProblem("url"))
    if category != NOTICE_ANONYMOUS_CATEGORY:
        if not notifier_name.strip():
            problems.append(NoticeProblem("notifier_name"))
        if not _EMAIL.fullmatch(notifier_email.strip()):
            problems.append(NoticeProblem("notifier_email"))
    if not good_faith:
        problems.append(NoticeProblem("good_faith"))
    return problems


def decision_problems(outcome: str, reason: str) -> list[str]:
    """A decision names a known outcome and records its reason (article 16(5))."""
    problems: list[str] = []
    if outcome not in NOTICE_OUTCOMES:
        problems.append("outcome")
    if not reason.strip():
        problems.append("reason")
    return problems


# --- Statement of reasons (DSA art. 17, CMP.5 part 3) and the transparency database (art. 24(5))

RESTRICTION_REMOVE: Final = "remove"
RESTRICTION_SUSPEND: Final = "suspend_account"
RESTRICTION_TERMINATE: Final = "terminate"  # the whole service ends (P2B art. 4, CMP.5 part 4)
RESTRICTIONS: Final = (RESTRICTION_REMOVE, RESTRICTION_SUSPEND, RESTRICTION_TERMINATE)
#: What the restriction is based on: a law (illegal content) or the platform terms.
GROUND_ILLEGAL: Final = "illegal_content"
GROUND_TERMS: Final = "terms"
GROUND_KINDS: Final = (GROUND_ILLEGAL, GROUND_TERMS)


PUBLIC_TEXT_LIMIT: Final = 2000  # the transparency database caps explanations here
_PUBLIC_PATTERNS: Final = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",  # email
        r"(?:https?://|www\.)\S+",  # URL
        r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b",  # IBAN
        r"\b(?:\d[ -]?){13,19}\b",  # card number
        r"(?<!\w)\+?\d[\d ()./-]{6,}\d",  # phone number
    )
)


def scrub_public_text(text: str) -> str:
    """`text` for a public database: emails, URLs, IBANs, card and phone numbers removed, at most
    `PUBLIC_TEXT_LIMIT` characters (art. 24(5): no personal data). Defence in depth: staff write
    this text for the public and are warned that it is published."""
    for pattern in _PUBLIC_PATTERNS:
        text = pattern.sub("[removed]", text)
    return text.strip()[:PUBLIC_TEXT_LIMIT]


def statement_problems(
    *,
    restriction: str,
    scope: str,
    facts: str,
    ground_kind: str,
    ground: str,
    transparency_explanation: str,
) -> list[str]:
    """Article 17(3) needs the restriction and its scope, the facts relied on and the legal or
    contractual ground; art. 24(5) the separate public explanation. The notice basis, automation
    and redress are filled in by the service."""
    problems: list[str] = []
    if restriction not in RESTRICTIONS:
        problems.append("restriction")
    if not scope.strip():
        problems.append("scope")
    if not facts.strip():
        problems.append("facts")
    if ground_kind not in GROUND_KINDS:
        problems.append("ground_kind")
    if not ground.strip():
        problems.append("ground")
    if not scrub_public_text(transparency_explanation):
        problems.append("transparency_explanation")
    return problems


def transparency_payload(
    *,
    restriction: str,
    ground_kind: str,
    target_kind: str,
    explanation: str,
    based_on_notice: bool,
    automated_detection: bool,
    automated_decision: bool,
    applied_on: dt.date,
    valid_until: dt.date | None,
    reference: str,
) -> dict[str, str]:
    """The article 24(5) record for the EU transparency database. Fixed codes plus the one
    free-text field written for the public (`explanation`, scrubbed and capped): never the
    vendor-facing facts, ground or scope, a vendor identity, URL or email. Field names follow the
    database's statement-of-reasons schema; the exact schema is verified when the submitter is
    built."""
    payload: dict[str, str] = {
        "puid": reference,
        "application_date": applied_on.isoformat(),
        "content_type": f"CONTENT_TYPE_{target_kind.upper()}",
        "territorial_scope": "EU",
        "decision_ground": (
            "DECISION_GROUND_ILLEGAL_CONTENT"
            if ground_kind == GROUND_ILLEGAL
            else "DECISION_GROUND_INCOMPATIBLE_CONTENT"
        ),
        "source_type": "SOURCE_ARTICLE_16" if based_on_notice else "SOURCE_VOLUNTARY",
        "automated_detection": "Yes" if automated_detection else "No",
        "automated_decision": (
            "AUTOMATED_DECISION_FULLY" if automated_decision else "AUTOMATED_DECISION_NOT_AUTOMATED"
        ),
    }
    if restriction == RESTRICTION_TERMINATE:
        payload["decision_account"] = "DECISION_ACCOUNT_TERMINATED"
    elif restriction == RESTRICTION_SUSPEND:
        payload["decision_account"] = "DECISION_ACCOUNT_SUSPENDED"
        if valid_until is not None:
            payload["end_date_account_restriction"] = valid_until.isoformat()
    else:
        payload["decision_visibility"] = "DECISION_VISIBILITY_CONTENT_REMOVED"
        if valid_until is not None:
            payload["end_date_visibility_restriction"] = valid_until.isoformat()
    key = (
        "illegal_content_explanation"
        if ground_kind == GROUND_ILLEGAL
        else "incompatible_content_explanation"
    )
    payload[key] = scrub_public_text(explanation)
    return payload


# --- Termination of the whole service (P2B art. 4, CMP.5 part 4)

#: The notice before the service of a business user is terminated is at least this long.
MIN_NOTICE_DAYS: Final = 30
#: The closed list of reasons that allow a shorter or no notice: a legal or regulatory
#: obligation that makes the notice impossible, repeated infringements of the terms, illegal
#: content (P2B art. 4(2) and (4)). Counsel reviews the wording; the code keys on the codes.
TERMINATION_EXCEPTIONS: Final = ("legal_obligation", "repeated_infringement", "illegal_content")


def notice_days(configured: int | None) -> int:
    """The notice period in days: the configured one, never below `MIN_NOTICE_DAYS`."""
    if configured is None:
        return MIN_NOTICE_DAYS
    return max(MIN_NOTICE_DAYS, int(configured))


def default_effective_at(issued_at: dt.datetime, days: int) -> dt.datetime:
    """When the termination takes effect if staff choose no date: the notice period after the
    notice (at least `MIN_NOTICE_DAYS`)."""
    return issued_at + dt.timedelta(days=notice_days(days))


def termination_problems(
    *,
    facts: str,
    ground: str,
    exception: str,
    issued_at: dt.datetime,
    effective_at: dt.datetime,
    days: int | None = None,
) -> list[str]:
    """What is wrong with a notice of termination, as field names (empty when fine).

    Without an exception the effective time is at least `notice_days(days)` after the notice;
    an exception from the closed list allows an earlier time, never one before the notice. The
    reasons (facts and ground) are always required: they are the statement of reasons (art. 4(1)).
    """
    problems: list[str] = []
    if not facts.strip():
        problems.append("facts")
    if not ground.strip():
        problems.append("ground")
    if exception and exception not in TERMINATION_EXCEPTIONS:
        problems.append("exception")
    allowed = exception in TERMINATION_EXCEPTIONS
    earliest = issued_at if allowed else default_effective_at(issued_at, notice_days(days))
    if effective_at < earliest:
        problems.append("effective_at")
    return problems


def termination_due(effective_at: dt.datetime, *, now: dt.datetime) -> bool:
    """Whether a pending termination takes effect: at its effective time, never before."""
    return now >= effective_at
