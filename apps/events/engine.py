"""Pure rules of the events module (spec 15). No Django, no ORM (ARC.18).

EVT.1 holds the status derivation and the RRULE check the model needs; EVT.2 adds the RRULE
subset validator and the expansion of occurrences here.
"""

import re

# Event types (spec 15 "Page models"). There is deliberately no closure type: a vendor's
# holiday or shop closure is a vendor setting (owner decision Q-73, VEN.20).
PROMOTION = "promotion"
FAIR = "fair"  # a trade fair or a meeting
OTHER = "other"
EVENT_TYPES = (PROMOTION, FAIR, OTHER)

# The denormalised `status` column (spec 15 "Status").
DRAFT = "draft"
PENDING = "pending"
PUBLISHED = "published"
CANCELLED = "cancelled"
HIDDEN = "hidden"
STATUSES = (DRAFT, PENDING, PUBLISHED, CANCELLED, HIDDEN)

_RULE_PART = re.compile(r"(?:^|;)\s*(COUNT|UNTIL)\s*=", re.IGNORECASE)


def event_status(*, live: bool, in_workflow: bool, cancelled: bool, hidden: bool) -> str:
    """The `status` of an event page from its Wagtail state.

    `hidden`: unpublished by staff (`hidden_at` set). `cancelled`: `cancelled_at` set; a live
    cancelled page stays online with a "Cancelled" message. A workflow in progress makes a page
    that is not live `pending`; an edit of a live page in review keeps it `published`. A page
    that is not live and not in review is a `draft`, also after it was unpublished.
    """
    if hidden:
        return HIDDEN
    if live:
        return CANCELLED if cancelled else PUBLISHED
    return PENDING if in_workflow else DRAFT


def has_count_and_until(rrule: str) -> bool:
    """Whether an RRULE names both COUNT and UNTIL, which RFC 5545 forbids (§3.3.10)."""
    parts = {match.upper() for match in _RULE_PART.findall(rrule)}
    return parts == {"COUNT", "UNTIL"}
