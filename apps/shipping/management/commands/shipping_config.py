"""Export or import a shipping configuration file, save or copy a template (SHP.13).

A thin adapter over `apps.shipping.config_io`; the file format is in spec 06. Imports and
template copies are dry runs that print the per-row report unless `--apply` is given.

    manage.py shipping_config export [--vendor PUBLIC_ID] [--csv]
    manage.py shipping_config import FILE [--vendor PUBLIC_ID] [--apply]
    manage.py shipping_config save-template FILE --code CODE --name NAME [--standard]
    manage.py shipping_config copy-template --vendor PUBLIC_ID [--code CODE] [--apply]

Without `--vendor`, export and import work on the platform's country groups and zones. A file
ending in `.csv` is read as CSV, any other as JSON.
"""

import json
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.shipping import config_format as fmt
from apps.shipping.config_io import (
    ImportResult,
    InvalidConfig,
    check_rows,
    copy_template,
    export_config,
    import_config,
    save_template,
    standard_template,
)
from apps.shipping.models import ShippingTemplate
from apps.vendors.models import Vendor

CTX = AuditContext(actor_label="command:shipping_config", source=AuditLog.Source.COMMAND)


class Command(BaseCommand):
    help = "Export or import a shipping configuration file; save or copy a shipping template."

    def add_arguments(self, parser: CommandParser) -> None:
        actions = parser.add_subparsers(dest="action", required=True)
        export = actions.add_parser("export", help="Print a configuration file.")
        export.add_argument("--vendor", help="Public id of the vendor; default: the platform.")
        export.add_argument("--csv", action="store_true", help="CSV instead of JSON.")
        load = actions.add_parser("import", help="Import a configuration file.")
        load.add_argument("file")
        load.add_argument("--vendor", help="Public id of the vendor; default: the platform.")
        load.add_argument("--apply", action="store_true", help="Write; default: dry run.")
        save = actions.add_parser("save-template", help="Create or change a template.")
        save.add_argument("file")
        save.add_argument("--code", required=True)
        save.add_argument("--name", required=True)
        save.add_argument("--standard", action="store_true", help="Propose it to vendors.")
        copy = actions.add_parser("copy-template", help="Copy a template into a vendor's.")
        copy.add_argument("--vendor", required=True, help="Public id of the vendor.")
        copy.add_argument("--code", help="Template code; default: the standard template.")
        copy.add_argument("--apply", action="store_true", help="Write; default: dry run.")

    def handle(self, *args: Any, **options: Any) -> None:
        action = options["action"]
        if action == "export":
            document = export_config(_owner(options["vendor"]))
            text = fmt.dump_csv(document) if options["csv"] else fmt.dump_json(document)
            self.stdout.write(text, ending="")
        elif action == "import":
            result = import_config(
                _read(options["file"]),
                vendor=_owner(options["vendor"]),
                ctx=CTX,
                dry_run=not options["apply"],
                source=Path(options["file"]).name,
            )
            self.report(result)
        elif action == "save-template":
            self.save_template(options)
        else:
            template = _template(options["code"])
            vendor = _vendor(options["vendor"])
            self.report(copy_template(template, vendor, ctx=CTX, dry_run=not options["apply"]))

    def save_template(self, options: dict[str, Any]) -> None:
        parsed = _read(options["file"])
        template = ShippingTemplate.objects.filter(code=options["code"]).first()
        template = template or ShippingTemplate(code=options["code"])
        try:
            if not parsed.ok:
                raise InvalidConfig(check_rows(parsed, vendor=None, platform=False))
            template.name = options["name"]
            template.is_standard = options["standard"]
            template.document = fmt.to_document(parsed)
            save_template(template, ctx=CTX)
        except InvalidConfig as exc:
            self.stdout.write(json.dumps([row.to_dict() for row in exc.rows], indent=2))
            raise CommandError(str(exc)) from exc
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        self.stdout.write(f"Saved the shipping template {template.code!r}.")

    def report(self, result: ImportResult) -> None:
        self.stdout.write(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        if not result.ok:
            raise CommandError("The file has errors: nothing was changed.")


def _read(path: str) -> fmt.ParsedConfig:
    try:
        if Path(path).stat().st_size > fmt.MAX_FILE_BYTES:
            raise CommandError(f"{path} is larger than {fmt.MAX_FILE_BYTES // 1024} KiB.")
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CommandError(f"Cannot read {path}: {exc}") from exc
    return fmt.parse_csv(text) if path.lower().endswith(".csv") else fmt.parse_json(text)


def _owner(public_id: str | None) -> Vendor | None:
    """The vendor whose configuration is meant, or None for the platform's."""
    return None if public_id is None else _vendor(public_id)


def _vendor(public_id: str) -> Vendor:
    try:
        vendor = Vendor.objects.filter(public_id=public_id).first()
    except ValidationError:
        vendor = None
    if vendor is None:
        raise CommandError(f"Unknown vendor {public_id!r}.")
    return vendor


def _template(code: str | None) -> ShippingTemplate:
    if code is None:
        template = standard_template()
    else:
        template = ShippingTemplate.objects.filter(code=code).first()
    if template is None:
        raise CommandError(f"Unknown shipping template {code or '(standard)'!r}.")
    return template
