"""AI origin of an image (CMP.8): the three values and the IPTC digital source type.

Pure (no ORM). Shared by `core.MediaImage.ai_origin` (the library image, one source of truth for
the file) and `catalog.ProductImage.ai_origin` (a product's own flag); `effective` combines them.
"""

AI_NONE = "none"
AI_GENERATED = "generated"
AI_EDITED = "edited"
AI_ORIGINS = (AI_NONE, AI_GENERATED, AI_EDITED)

# IPTC NewsCodes "digital source type" terms (the vocabulary of the IPTC photo metadata and C2PA
# standards): an image made by AI, and a real image altered with AI.
IPTC_SOURCE_TYPES = {
    AI_GENERATED: "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
    AI_EDITED: "http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia",
}


def iptc_source_type(ai_origin: str) -> str:
    """The IPTC digital source type URI of an AI image; empty for an image with no AI origin."""
    return IPTC_SOURCE_TYPES.get(ai_origin, "")


def effective(*origins: str) -> str:
    """The origin to show when several rows speak for one image: `generated` beats `edited`
    beats `none`, so an AI image is never shown unlabelled because one of the rows forgot it."""
    for wanted in (AI_GENERATED, AI_EDITED):
        if wanted in origins:
            return wanted
    return AI_NONE
