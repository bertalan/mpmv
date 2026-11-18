"""Interface-string catalogs without GNU gettext (I18N.7).

`makemessages` and `compilemessages` need the GNU gettext tools, which neither the dev machines
nor the app image carry. This script does the same job with Django's own template tokenizer and
`polib`:

    python tools/i18n_catalog.py extract   # rewrite locale/<lang>/LC_MESSAGES/django.po
    python tools/i18n_catalog.py compile   # write the django.mo next to each django.po
    python tools/i18n_catalog.py review-export <lang> [--all] [--pending] [--out FILE]
                                           # CSV of the sensitive entries for a translator
    python tools/i18n_catalog.py check     # exit 1 when a catalog is stale, has an untranslated
                                           # entry or its .mo does not match its .po

`extract` keeps every existing translation, drops msgids no longer in the code, and adds new ones
with an empty msgstr (which `check` then reports). The scanned scope is `SCOPES`: the Manager,
the account and MFA pages and the shared theme layouts and components they render. Both files are versioned,
so the application needs no build step to translate; `check` guards that they stay in step.
"""

from __future__ import annotations

import argparse
import ast
import csv
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
LOCALE = ROOT / "locale"
LANGUAGES = ("it", "de", "fr", "es")  # grows with I18N.7 slices
# The public event pages (EVT.6): their templates, the listing module and the type labels.
EVENT_FILES = (
    "apps/events/listing.py",
    "apps/events/models.py",
    *(
        f"apps/events/templates/events/{name}.html"
        for name in (
            "event_calendar",
            "event_index_page",
            "event_page",
            "event_past",
            "_filters",
            "_links",
            "_results",
            "_summary",
            "_subscribe",
        )
    ),
)
SCOPES = (
    "apps/manager",
    "apps/accounts",
    "apps/orders/templates",
    "apps/seo",
    "apps/orders/ui",
    "apps/vendors/templates/email/vendors/profile_reverted",
    "apps/events/templates/email",  # the event moderation emails (EVT.11)
    "apps/orders/customer_views.py",  # the customer area labels (ORD.7)
    "apps/compliance/forms.py",  # WithdrawalLookupForm (CMP.6): its help texts are translated
    "apps/vendors/admin.py",  # help texts of the plan and commission rule forms (ARC.30)
    "apps/cms/models.py",  # HelpEntry (ARC.32): its help texts are translated
    "apps/vendors/models.py",
    "apps/vendors/templates/vendors/terms",  # the public sales terms page (VEN.35)
    "apps/vendors/templates/vendors/apply",  # the vendor application pages (VEN.22), formal
    "apps/vendors/templates/email/vendors/application_confirm",  # the application emails (VEN.22)
    "apps/vendors/templates/email/vendors/application_exists",
    "apps/vendors/templates/email/vendors/application_reminder",
    "apps/vendors/forms.py",  # the application and confirm forms (VEN.22)
    "themes/base/templates/layouts",
    "themes/ride/templates/components/header.html",  # the Bootstrap navbar and offcanvas (THM.15)
    "themes/ride/templates/components/footer.html",  # social nav label, back to top (THM.15)
    # Brand review models (CAT.20): the rest of the catalog app joins with the storefront I18N.
    "apps/catalog/taxonomy.py",
    "apps/catalog/taxonomy_admin.py",
    # The product page and what it includes (CAT.7); the listings join with the storefront I18N.
    "apps/catalog/templates/catalog/product.html",
    "apps/catalog/templates/catalog/includes/stock_status.html",
    "apps/catalog/templates/catalog/includes/product_card.html",
    # The note under every consumer price (TAX.12, owner wording 2026-11-16), in all languages.
    "apps/tax/prices.py",
    # Temporary staff "Site links" page (OPS.12); remove before LIVE.
    "apps/seo/site_links.py",
    "apps/seo/templates/seo/site_links.html",
    # The customer privacy page (I18N.13 follow-up): informal register, "Your data" has its own context.
    "apps/compliance/templates/compliance/privacy_account.html",
    "themes/ride/templates/compliance/privacy_account.html",
    # The staff note of the unused-images list (CAT.25): the rest of that page is English admin.
    "apps/catalog/templates/catalog/admin/orphan_images_old_versions.html",
    # The customer help pages (ARC.37): informal register.
    "apps/cms/templates/cms/help_guide_index.html",
    "apps/cms/templates/cms/help_guide_section.html",
    *EVENT_FILES,  # the public event pages (EVT.6)
)
# Single storefront modules whose strings the Manager texts quote (shop policy headings).
EXTRA_FILES = (
    "apps/vendors/services.py",
    "apps/cms/templatetags/cms_tags.py",  # the header's "Site links" item (OPS.12)
    "apps/cms/slugs.py",  # the reserved-slug error of the page form (ARC.37)
)
# Single Python modules scanned in full: customer-facing messages of apps that are not in SCOPES.
FILES = (
    "apps/orders/messages.py",
    "apps/orders/amendments.py",
    "apps/orders/amendment_engine.py",  # the classification reasons and refusals (ORD.20)
    "apps/orders/amendment_notices.py",
)
# Apps scanned for `help_text=` strings only (ARC.31: field help is translated; the rest of these
# apps joins SCOPES with I18N.7). Python files, each string inside a `help_text=` argument.
HELP_TEXT_SCOPES = (
    "apps/catalog",
    "apps/cms",
    "apps/compliance",
    "apps/core",
    "apps/events",
    "apps/ingest",
    "apps/shipping",
    "apps/tax",
    "apps/themes",
    "apps/vendors",
)
# Every language scans the same scope (I18N.7 slice 2): a language listed here would scan only
# the given paths, so the dict stays empty and `SCOPES` applies to all of them.
LANGUAGE_SCOPES: dict[str, tuple[str, ...]] = {}
# Theme files rendered by the Manager and account pages (the rest of the theme is storefront).
THEME_FILES = (
    "400.html",
    "403.html",
    "403_csrf.html",
    "404.html",
    "500.html",
    *(
        f"components/{n}.html"
        for n in (
            "alert",
            "button",
            "cart_line",
            "empty_state",
            "error_summary",
            "field",
            "footer",
            "header",
            "help_entry",
            "icon",
            "language_switcher",
            "order_summary",
            "pagination",
            "quantity_stepper",
            "skip_link",
            "step_indicator",
            "table",
            "user_tools",
        )
    ),
)
# A single-file scope may be narrowed to the classes whose strings the catalog translates.
ONLY_CLASSES = {
    "apps/compliance/forms.py": ("WithdrawalLookupForm",),
    "apps/cms/models.py": ("HelpEntry",),
    "apps/events/models.py": ("EventType", "Attendance"),  # the labels the pages show
}
SKIP_PARTS = {"tests", "migrations", "static", "node_modules"}

SIMPLE = {"gettext", "gettext_lazy", "gettext_noop", "_", "ugettext"}
CONTEXT = {"pgettext", "pgettext_lazy"}
PLURAL = {"ngettext", "ngettext_lazy"}
CONTEXT_PLURAL = {"npgettext", "npgettext_lazy"}


def _files(scopes: tuple[str, ...] = SCOPES) -> Iterator[Path]:
    for scope in scopes:
        base = ROOT / scope
        for path in [base] if base.is_file() else sorted(base.rglob("*")):
            rel = path.relative_to(ROOT)
            if SKIP_PARTS & set(rel.parts) or path.suffix not in {".py", ".html", ".txt"}:
                continue
            yield path
    if scopes == SCOPES:
        for extra in (*FILES, *EXTRA_FILES):
            yield ROOT / extra
        for name in THEME_FILES:
            yield ROOT / "themes/base/templates" / name


def _source(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return text
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mpmv.settings.dev")
    from django.utils.translation.template import templatize

    return templatize(text, origin=str(path))


def _const(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


_LITERAL = r"""u?(?:'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")"""
_CALL = re.compile(
    r"\b(gettext|ngettext|pgettext|npgettext|_)\(\s*("
    + _LITERAL
    + r")(?:\s*,\s*("
    + _LITERAL
    + r"))?(?:\s*,\s*("
    + _LITERAL
    + r"))?"
)


def _lit(text: str | None) -> str | None:
    return ast.literal_eval(text.lstrip("u")) if text else None


def _template_entries(path: Path) -> Iterator[tuple[str, str | None, str | None, int]]:
    """Template strings, read from Django's own `templatize` output (junk around calls)."""
    for m in _CALL.finditer(_source(path)):
        name, first, second, third = (
            m.group(1),
            _lit(m.group(2)),
            _lit(m.group(3)),
            _lit(m.group(4)),
        )
        line = 0
        if name in {"gettext", "_"} and first:
            yield first, None, None, line
        elif name == "ngettext" and first and second:
            yield first, None, second, line
        elif name == "pgettext" and first and second:
            yield second, first, None, line
        elif name == "npgettext" and first and second and third:
            yield second, first, third, line


def _entries(path: Path) -> Iterator[tuple[str, str | None, str | None, int]]:
    """(msgid, msgctxt, msgid_plural, line) for each gettext call in `path`."""
    if path.suffix != ".py":
        yield from _template_entries(path)
        return
    tree = ast.parse(path.read_text(encoding="utf-8"))
    only = ONLY_CLASSES.get(str(path.relative_to(ROOT)))
    roots: list[ast.AST] = (
        [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in only] if only else [tree]
    )
    for node in (n for root in roots for n in ast.walk(root)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        args = [_const(a) for a in node.args]
        if name in SIMPLE and args and args[0]:
            yield args[0], None, None, node.lineno
        elif name in CONTEXT and len(args) >= 2 and args[0] and args[1]:
            yield args[1], args[0], None, node.lineno
        elif name in PLURAL and len(args) >= 2 and args[0] and args[1]:
            yield args[0], None, args[1], node.lineno
        elif name in CONTEXT_PLURAL and len(args) >= 3 and args[0] and args[1] and args[2]:
            yield args[1], args[0], args[2], node.lineno


def _help_files() -> Iterator[Path]:
    for scope in HELP_TEXT_SCOPES:
        for path in sorted((ROOT / scope).rglob("*.py")):
            if not SKIP_PARTS & set(path.relative_to(ROOT).parts):
                yield path


def _help_entries(path: Path) -> Iterator[tuple[str, str | None, str | None, int]]:
    """The gettext calls inside a `help_text=` argument of `path`, and nothing else."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    inside: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            inside.extend(kw.value for kw in node.keywords if kw.arg == "help_text")
    seen: set[int] = set()
    for root in inside:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call) or id(node) in seen:
                continue
            seen.add(id(node))
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            args = [_const(a) for a in node.args]
            if name in SIMPLE and args and args[0]:
                yield args[0], None, None, node.lineno


def collect(
    scopes: tuple[str, ...] = SCOPES,
) -> dict[tuple[str, str | None], tuple[str | None, list[str]]]:
    found: dict[tuple[str, str | None], tuple[str | None, list[str]]] = {}
    sources = [(path, _entries) for path in _files(scopes)]
    if scopes == SCOPES:
        sources += [(path, _help_entries) for path in _help_files()]
    for path, read in sources:
        rel = str(path.relative_to(ROOT))
        for msgid, ctx, plural, _line in read(path):
            plural0, places = found.get((msgid, ctx), (plural, []))
            if rel not in places:
                places.append(rel)
            found[(msgid, ctx)] = (plural0 or plural, places)
    return found


def _po_path(lang: str) -> Path:
    return LOCALE / lang / "LC_MESSAGES" / "django.po"


def extract() -> None:
    for lang in LANGUAGES:
        found = collect(LANGUAGE_SCOPES.get(lang, SCOPES))
        path = _po_path(lang)
        old = polib.pofile(str(path)) if path.exists() else None
        known = {(e.msgid, e.msgctxt): e for e in old} if old else {}
        po = polib.POFile(wrapwidth=100)
        po.metadata = {
            "Project-Id-Version": "MPMV",
            "Language": lang,
            "MIME-Version": "1.0",
            "Content-Type": "text/plain; charset=UTF-8",
            "Content-Transfer-Encoding": "8bit",
            "Plural-Forms": "nplurals=2; plural=(n != 1);",
        }
        for (msgid, ctx), (plural, places) in sorted(
            found.items(), key=lambda kv: kv[0][0].lower()
        ):
            prev = known.get((msgid, ctx))
            entry = polib.POEntry(
                msgid=msgid,
                msgctxt=ctx,
                msgid_plural=plural or "",
                occurrences=[(p, "") for p in sorted(places)],
                tcomment=prev.tcomment if prev else "",
            )
            if plural:
                strs = dict(prev.msgstr_plural) if prev and prev.msgstr_plural else {}
                entry.msgstr_plural = {0: strs.get(0, ""), 1: strs.get(1, "")}
            else:
                entry.msgstr = prev.msgstr if prev else ""
            po.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        po.save(str(path))
        print(f"{path.relative_to(ROOT)}: {len(po)} entries")


# Entries a human (professional translator, and counsel for legal and tax wording) must review
# before a language is offered to customers (owner decision 2026-11-16). An entry is sensitive
# when any of its `#:` source references starts with one of these prefixes; the first matching
# group names it. Marking an entry reviewed: a translator comment line `# reviewed` in the .po.
SENSITIVE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "order emails and documents",
        ("apps/orders/templates/email/", "apps/orders/templates/documents/"),
    ),
    ("compliance forms", ("apps/compliance/",)),
    (
        "DSA statements of reasons (EVT.11)",
        (
            "apps/manager/templates/manager/shop/statements.html",
            "apps/manager/statement_views.py",
        ),
    ),
    (
        "event moderation emails (EVT.11)",
        ("apps/events/templates/email/events/", "apps/events/notifications.py"),
    ),
    (
        "sales terms",
        (
            "apps/vendors/templates/vendors/terms/",
            "apps/manager/templates/manager/shop/sales_terms.html",
            "apps/manager/sales_terms_views.py",
        ),
    ),
    ("price notes and tax", ("apps/tax/",)),
    (
        "cart and order summary components",
        (
            "themes/base/templates/components/cart_line.html",
            "themes/base/templates/components/order_summary.html",
            "themes/base/templates/components/step_indicator.html",
            "themes/base/templates/components/quantity_stepper.html",
        ),
    ),
    ("orders, checkout and order changes (ORD.20)", ("apps/orders/",)),
)
REVIEWED_MARK = "reviewed"
REVIEW_COLUMNS = ("msgid", "msgctxt", "msgid_plural", "sources", "msgstr", "group")


def sensitive_group(entry: polib.POEntry) -> str | None:
    paths = [p for p, _line in entry.occurrences]
    for group, prefixes in SENSITIVE_GROUPS:
        if any(p.startswith(prefixes) for p in paths):
            return group
    return None


def is_reviewed(entry: polib.POEntry) -> bool:
    return any(line.strip() == REVIEWED_MARK for line in entry.tcomment.splitlines())


def review_rows(lang: str, *, everything: bool = False, pending: bool = False) -> list[list[str]]:
    rows = []
    for e in polib.pofile(str(_po_path(lang))):
        group = sensitive_group(e)
        if (group is None and not everything) or (pending and is_reviewed(e)):
            continue
        forms = (
            " | ".join(f"[{i}] {v}" for i, v in sorted(e.msgstr_plural.items()))
            if e.msgid_plural
            else e.msgstr
        )
        rows.append(
            [
                e.msgid,
                e.msgctxt or "",
                e.msgid_plural,
                " ".join(p for p, _line in e.occurrences),
                forms,
                group or "other",
            ]
        )
    return rows


def _safe_cell(value: str) -> str:
    """Spreadsheet formula injection: a cell starting with = + - @ tab or CR gets a leading apostrophe."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def review_export(
    lang: str, out: Path | None = None, *, everything: bool = False, pending: bool = False
) -> int:
    rows = review_rows(lang, everything=everything, pending=pending)
    handle = out.open("w", encoding="utf-8", newline="") if out else sys.stdout
    try:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(REVIEW_COLUMNS)
        writer.writerows([_safe_cell(cell) for cell in row] for row in rows)
    finally:
        if out:
            handle.close()
    return len(rows)


def _missing(po: polib.POFile) -> list[str]:
    out = []
    for e in po:
        done = all(e.msgstr_plural.values()) if e.msgid_plural else bool(e.msgstr)
        if not done:
            out.append(e.msgid)
    return out


def compile_all() -> None:
    for lang in LANGUAGES:
        po = polib.pofile(str(_po_path(lang)))
        mo = _po_path(lang).with_suffix(".mo")
        po.save_as_mofile(str(mo))
        print(f"{mo.relative_to(ROOT)}: {len(po)} entries")


def check() -> int:
    bad = 0
    for lang in LANGUAGES:
        found = collect(LANGUAGE_SCOPES.get(lang, SCOPES))
        path = _po_path(lang)
        po = polib.pofile(str(path))
        have = {(e.msgid, e.msgctxt) for e in po}
        if have != set(found):
            print(f"{lang}: catalog is stale, run `python tools/i18n_catalog.py extract`")
            bad += 1
        if missing := _missing(po):
            print(f"{lang}: {len(missing)} untranslated, e.g. {missing[0]!r}")
            bad += 1
        mo = path.with_suffix(".mo")
        fresh = polib.mofile(str(mo)) if mo.exists() else None
        if fresh is None or {(e.msgid, e.msgctxt): (e.msgstr, e.msgstr_plural) for e in fresh} != {
            (e.msgid, e.msgctxt): (e.msgstr, e.msgstr_plural) for e in po if not e.obsolete
        }:
            print(f"{lang}: django.mo does not match django.po, run `compile`")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "review-export":
        ap = argparse.ArgumentParser(prog="i18n_catalog.py review-export")
        ap.add_argument("lang", choices=LANGUAGES)
        ap.add_argument("--out", type=Path)
        ap.add_argument("--all", action="store_true", dest="everything")
        ap.add_argument("--pending", action="store_true")
        ns = ap.parse_args(sys.argv[2:])
        n = review_export(ns.lang, ns.out, everything=ns.everything, pending=ns.pending)
        print(f"{n} entries", file=sys.stderr)
        raise SystemExit(0)
    if command == "extract":
        extract()
    elif command == "compile":
        compile_all()
    elif command == "check":
        raise SystemExit(check())
    else:
        raise SystemExit(__doc__)
