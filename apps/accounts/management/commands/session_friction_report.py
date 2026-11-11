"""Prompts and elevations per user per day, for the D-32 friction review (CORE.25)."""

import datetime as dt
from argparse import ArgumentParser
from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts import levels
from apps.core.models import AuditLog


class Command(BaseCommand):
    help = "Count per-action prompts and elevations (session and single) per user per day."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--days", type=int, default=30, help="How many days back (default 30)")

    def handle(self, *args: Any, **options: Any) -> None:
        since = timezone.now() - dt.timedelta(days=options["days"])
        rows = AuditLog.objects.filter(
            created_at__gte=since,
            action__in=[levels.PROMPT, levels.SESSION_STARTED, levels.SINGLE_GRANTED],
        ).values_list("created_at", "actor_label", "action")
        prompts: Counter[tuple[dt.date, str]] = Counter()
        elevations: Counter[tuple[dt.date, str]] = Counter()
        for created_at, label, action in rows:
            key = (timezone.localdate(created_at), label)
            (prompts if action == levels.PROMPT else elevations)[key] += 1
        self.stdout.write("day  user  prompts  elevations")
        for day, label in sorted(prompts.keys() | elevations.keys()):
            self.stdout.write(
                f"{day}  {label}  {prompts[(day, label)]}  {elevations[(day, label)]}"
            )
