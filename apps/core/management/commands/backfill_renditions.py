"""Queue the render job of one group for every image that lacks its renditions (ORD.40).

An image saved before a preset existed has no renditions for it. The page falls back to another
preset's renditions meanwhile and never builds one; this command makes the real ones in the
background. Run it once after the release that adds the `thumb` group (cart line thumbnails).
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core import images, rendition_monitor


class Command(BaseCommand):
    help = (
        "Queue the render job of a group (default: thumb) for every image without its "
        "renditions, in batches. Safe to rerun; --dry-run only counts."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--group", default="thumb", choices=sorted(images.RENDER_GROUPS))
        parser.add_argument("--batch-size", type=int, default=rendition_monitor.CHUNK)
        parser.add_argument("--dry-run", action="store_true", help="Queue nothing.")

    def handle(
        self,
        *args: Any,
        group: str,
        batch_size: int,
        dry_run: bool = False,
        **options: Any,
    ) -> None:
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1.")
        scanned, queued = rendition_monitor.backfill_group(
            group, batch_size=batch_size, dry_run=dry_run
        )
        verb = "would queue" if dry_run else "queued"
        self.stdout.write(f"Scanned {scanned} image(s); {verb} {queued} {group} job(s).")
