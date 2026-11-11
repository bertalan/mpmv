#!/usr/bin/env -S uv run
"""Build docs/dev/public-forms.md, the register of public forms (SEC.13).

uv run tools/build_public_forms.py           # regenerate
uv run tools/build_public_forms.py --check   # exit 1 if the committed file is stale

The rows come from the form declarations in the code (apps/core/public_forms.py); the same sweep
fails `manage.py check` on an undeclared anonymous POST route. Needs no database.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mpmv.settings.test")

import django  # noqa: E402

django.setup()

from apps.core.public_forms import REGISTER_PATH, public_form_report, render_register  # noqa: E402


def main(argv: list[str]) -> int:
    text = render_register(public_form_report())
    target = ROOT / REGISTER_PATH
    if "--check" in argv:
        if not target.exists() or target.read_text() != text:
            print(f"{REGISTER_PATH} is stale: run `make docs` and commit it.", file=sys.stderr)
            return 1
        return 0
    target.write_text(text)
    print(f"wrote {REGISTER_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
