"""Background jobs of the catalog app (ADR-0004, docs/dev/background-jobs.md)."""

from apps.core import tasks as core_tasks
from apps.core.tasks import task


@task("catalog.render_product_image")
def render_product_image(image_id: int) -> None:
    """Kept so that jobs queued before `core.render_image` existed still run: it does the same."""
    core_tasks.render_image(image_id=image_id)
