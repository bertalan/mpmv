"""Interface-string catalogs without GNU gettext (I18N.7).

`makemessages` and `compilemessages` need the GNU gettext tools, which neither the dev machines
nor the app image carry. This script does the same job with Django's own template tokenizer and
`polib`:

    python tools/i18n_catalog.py extract   # rewrite locale/<lang>/LC_MESSAGES/django.po
    python tools/i18n_catalog.py compile   # write the django.mo next to each django.po
    python tools/i18n_catalog.py check     # exit 1 when a catalog is stale, has an untranslated
                                           # entry or its .mo does not match its .po

`extract` keeps every existing translation, drops msgids no longer in the code, and adds new ones
with an empty msgstr (which `check` then reports). The scanned scope is `SCOPES`: the Manager,
the account and MFA pages and the shared theme layouts and components they render. Both files are versioned,
so the application needs no build step to translate; `check` guards that they stay in step.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
LOCALE = ROOT / "locale"
LANGUAGES = ("it",)  # grows with I18N.7 slices: de, fr, es
SCOPES = (
    "apps/manager",
    "apps/accounts",
    "apps/orders/templates",
    "apps/compliance/forms.py",  # WithdrawalLookupForm (CMP.6): its help texts are translated
    "apps/vendors/admin.py",  # help texts of the plan and commission rule forms (ARC.30)
    "apps/cms/models.py",  # HelpEntry (ARC.32): its help texts are translated
    "apps/vendors/models.py",
    "themes/base/templates/layouts",
    # Brand review models (CAT.20): the rest of the catalog app joins with the storefront I18N.
    "apps/catalog/taxonomy.py",
    "apps/catalog/taxonomy_admin.py",
    # Temporary staff "Site links" page (OPS.12); remove before LIVE.
    "apps/seo/site_links.py",
    "apps/seo/templates/seo/site_links.html",
)
# Single storefront modules whose strings the Manager texts quote (shop policy headings).
EXTRA_FILES = (
    "apps/vendors/services.py",
    "apps/cms/templatetags/cms_tags.py",  # the header's "Site links" item (OPS.12)
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
            "empty_state",
            "error_summary",
            "field",
            "footer",
            "header",
            "help_entry",
            "icon",
            "language_switcher",
            "pagination",
            "skip_link",
            "table",
            "user_tools",
        )
    ),
)
# A single-file scope may be narrowed to the classes whose strings the catalog translates.
ONLY_CLASSES = {
    "apps/compliance/forms.py": ("WithdrawalLookupForm",),
    "apps/cms/models.py": ("HelpEntry",),
}
SKIP_PARTS = {"tests", "migrations", "static", "node_modules"}

SIMPLE = {"gettext", "gettext_lazy", "gettext_noop", "_", "ugettext"}
CONTEXT = {"pgettext", "pgettext_lazy"}
PLURAL = {"ngettext", "ngettext_lazy"}
CONTEXT_PLURAL = {"npgettext", "npgettext_lazy"}


def _files() -> Iterator[Path]:
    for scope in SCOPES:
        base = ROOT / scope
        for path in [base] if base.is_file() else sorted(base.rglob("*")):
            rel = path.relative_to(ROOT)
            if SKIP_PARTS & set(rel.parts) or path.suffix not in {".py", ".html", ".txt"}:
                continue
            yield path
    for extra in EXTRA_FILES:
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


def collect() -> dict[tuple[str, str | None], tuple[str | None, list[str]]]:
    found: dict[tuple[str, str | None], tuple[str | None, list[str]]] = {}
    sources = [(path, _entries) for path in _files()]
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
    found = collect()
    for lang in LANGUAGES:
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
    found = collect()
    bad = 0
    for lang in LANGUAGES:
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
    if command == "extract":
        extract()
    elif command == "compile":
        compile_all()
    elif command == "check":
        raise SystemExit(check())
    else:
        raise SystemExit(__doc__)
