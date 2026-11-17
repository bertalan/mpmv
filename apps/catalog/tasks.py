"""Background jobs of the catalog app (ADR-0004, docs/dev/background-jobs.md)."""

from apps.core.tasks import task

from . import media


@task("catalog.render_product_image")
def render_product_image(image_id: int) -> None:
    """Make the AVIF, WebP and JPEG renditions of a product image's presets (CAT.4).

    Queued by `media.upload_image` in the upload's transaction, so the upload never waits for
    the encoders. Safe to run twice: a rendition that exists is reused.
    """
    media.render_renditions(image_id)
