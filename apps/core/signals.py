"""Signals of the core app."""

from typing import Any

from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from . import images
from .models import MediaImage, PlatformOperator
from .tasks import enqueue, render_image

_MISSING = object()


@receiver(post_init, sender=MediaImage, dispatch_uid="core_media_image_file_name")
def remember_file_name(sender: Any, instance: MediaImage, **kwargs: Any) -> None:
    """Keep the file name the row was loaded with, to tell a new or replaced file from a re-save."""
    loaded = instance.pk is not None and "file" not in instance.get_deferred_fields()
    instance._rendered_file_name = instance.file.name if loaded else _MISSING


@receiver(post_save, sender=MediaImage, dispatch_uid="core_media_image_renditions")
def queue_renditions(
    sender: Any,
    instance: MediaImage,
    created: bool,
    raw: bool = False,
    update_fields: frozenset[str] | None = None,
    **kwargs: Any,
) -> None:
    """Queue the job that makes the renditions of every preset when the file is new or replaced,
    so no page serves the full-size original for good. Nothing is queued for a fixture load, a
    save that leaves out `file`, or a save of an unchanged file (title, alt, collection, focal
    point). `enqueue` writes the job row in the saving transaction: it exists only if the save
    commits. One job per image and group waits at a time (queueing lock), so a second save adds
    none."""
    if raw:
        return
    if update_fields is not None and "file" not in update_fields:
        return
    previous = getattr(instance, "_rendered_file_name", _MISSING)
    instance._rendered_file_name = instance.file.name
    if not created and previous == instance.file.name:
        return
    for group in images.RENDER_GROUPS:  # hero first: jobs run in the order they were queued
        enqueue(
            render_image,
            image_id=instance.pk,
            group=group,
            queueing_lock=f"core.render_image:{instance.pk}:{group}",
        )


@receiver(post_save, sender=PlatformOperator, dispatch_uid="core_operator_forget_zone")
def forget_operator_zone(sender: Any, **kwargs: Any) -> None:
    """ARC.27: a saved operator row may change the business time zone the request remembered."""
    from .services import forget_business_tz

    forget_business_tz()
