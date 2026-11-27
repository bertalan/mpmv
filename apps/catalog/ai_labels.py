"""AI Act transparency for product content (CMP.8): origin values, labels and metadata flags.

Pure (no ORM). Images: `ProductImage.ai_origin` is `none`, `generated` or `edited`; the product
page shows the visible label next to the image and exposes the IPTC digital source type
(`digitalSourceType`) in a data attribute and in the product JSON-LD `ImageObject`.
Text: `ProductTranslation.origin` is `human` or `machine`; machine text is never published as
reviewed without a human review (`may_leave_draft`).
"""

from apps.core.ai_origin import (
    AI_EDITED,
    AI_GENERATED,
    AI_NONE,
    AI_ORIGINS,
    IPTC_SOURCE_TYPES,
    effective,
    iptc_source_type,
)

__all__ = [
    "AI_EDITED",
    "AI_GENERATED",
    "AI_NONE",
    "AI_ORIGINS",
    "IPTC_SOURCE_TYPES",
    "ORIGIN_HUMAN",
    "ORIGIN_MACHINE",
    "TEXT_ORIGINS",
    "effective",
    "iptc_source_type",
    "may_leave_draft",
]

ORIGIN_HUMAN = "human"
ORIGIN_MACHINE = "machine"
TEXT_ORIGINS = (ORIGIN_HUMAN, ORIGIN_MACHINE)


def may_leave_draft(origin: str, *, reviewed_by_person: bool) -> bool:
    """True when a text may move from machine draft to reviewed or published.

    Human text always may; machine text only after a person reviewed it (AI Act article 50,
    CMP.8: unreviewed machine text is never published as if reviewed).
    """
    return origin != ORIGIN_MACHINE or reviewed_by_person
