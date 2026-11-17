"""Signals of the core app."""

from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import MediaImage
from .tasks import enqueue, render_image


@receiver(post_save, sender=MediaImage, dispatch_uid="core_media_image_renditions")
def queue_renditions(sender: Any, instance: MediaImage, **kwargs: Any) -> None:
    """Every save of an image queues the job that makes its renditions (all presets), so no
    page serves the full-size original for good. The job row is written in the saving
    transaction: it exists only if the save commits. The job is idempotent, so a re-save is
    harmless."""
    enqueue(render_image, image_id=instance.pk)
