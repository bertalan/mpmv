"""Background jobs of the CMS (ADR-0004, docs/dev/background-jobs.md)."""

from django.core.management import call_command

from apps.core.tasks import NO_RETRY, locked, periodic, task


@periodic("*/5 * * * *")
@locked()
@task("cms.publish_scheduled", retry=NO_RETRY)
def publish_scheduled(timestamp: int) -> None:
    """Every five minutes: publish the pages whose go-live has come and unpublish the expired
    ones (Wagtail's `publish_scheduled`), so a scheduled article (CMS.3) appears on time."""
    call_command("publish_scheduled")
