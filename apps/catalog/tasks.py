"""Background jobs of the catalog app (ADR-0004, docs/dev/background-jobs.md)."""

from apps.core import tasks as core_tasks
from apps.core.tasks import task


# TODO(2026-11-17): remove this alias after one release, once no queued job uses its name
# (`core.list_jobs` shows none); `core.render_image` replaces it.
@task("catalog.render_product_image")
def render_product_image(image_id: int) -> None:
    """Kept so that jobs queued before `core.render_image` existed still run: it does the same."""
    core_tasks.render_image(image_id=image_id)
