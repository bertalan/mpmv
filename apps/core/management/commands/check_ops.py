"""Run the operations checks for the host (OPS.2): database disk space, WAL archiving, backup age.

Exit status 0 when all is well, 1 (CommandError) when an alert was raised. Staff are e-mailed
once per alert and repeat window, so the host may run this every few minutes.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.alerts import run_ops_checks


class Command(BaseCommand):
    help = "Check disk space, WAL archiving and backup age; alert staff on a problem."

    def handle(self, *args: Any, **options: Any) -> None:
        alerts = run_ops_checks()
        for alert in alerts:
            self.stdout.write(f"ALERT {alert.key}: {alert.summary}")
        if alerts:
            raise CommandError(f"{len(alerts)} alert(s) raised")
        self.stdout.write("ok: no operations alert")
