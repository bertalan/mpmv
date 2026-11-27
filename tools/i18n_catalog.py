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
            "_event_map",
            "_index_map",
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
    "apps/fitment/templates/email",  # the garage inactivity notice (FIT.5 3/3)
    "apps/fitment/services.py",  # the publish gate message (FIT.18), shown to vendors and staff
    "apps/payments/stray.py",  # closing a stray-payment alert (PAY.4), staff, formal
    "apps/accounts",
    "apps/orders/templates",
    "apps/seo",
    "apps/orders/ui",
    "apps/shipping/templates/shipping",  # the closure notice (VEN.20)
    "apps/vendors/templates/email",  # the vendor emails (I18N.20 3c)
    "apps/events/templates/email",  # the event moderation emails (EVT.11)
    "apps/catalog/templates/email",  # brand rejected notice to the vendor (I18N.20 3c)
    "apps/core/templates/email",  # the operations alert to staff (I18N.20 3c)
    "apps/orders/customer_views.py",  # the customer area labels (ORD.7)
    "apps/compliance/forms.py",  # WithdrawalLookupForm (CMP.6): its help texts are translated
    "apps/compliance/views.py",  # withdrawal and notice errors, the form PDF 503 (I18N.20)
    "apps/compliance/templates/email",  # the compliance emails (I18N.20 3c)
    "apps/vendors/admin.py",  # help texts of the plan and commission rule forms (ARC.30)
    "apps/cms/models.py",  # HelpEntry (ARC.32): its help texts are translated
    "apps/vendors/models.py",
    "apps/vendors/templates/vendors/terms",  # the public sales terms page (VEN.35)
    "apps/vendors/templates/documents/vendors",  # the sales terms PDF (VEN.36), same strings as the page
    "apps/vendors/templates/vendors/apply",  # the vendor application pages (VEN.22), formal
    "apps/vendors/forms.py",  # the application and confirm forms (VEN.22)
    "apps/vendors/templates/vendors/claim",  # the vendor claim pages (I18N.10 follow-up)
    "apps/vendors/views.py",  # the claim page refusals
    "apps/themes/admin_views.py",  # the palette builder page and its live check (THM.34)
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
    # The storefront catalog (I18N.20 part 1): listing, search, the product page includes, the
    # spec table labels (CAT.22), the fitment summary (FIT.9) and the ride catalog overrides.
    "apps/catalog/templates/catalog/listing.html",
    "apps/catalog/templates/catalog/search.html",
    "apps/catalog/templates/catalog/includes",
    "apps/catalog/product_pages.py",
    "apps/fitment/product_summary.py",
    "themes/ride/templates/catalog",
    "themes/base/templates/components/gallery.html",  # the product gallery (variant images)
    "themes/ride/templates/components/gallery.html",
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
    # The rest of the public event surface (I18N.20 part 2): the vendor storefront list and the
    # feed labels (RSS and calendar titles, the text of each calendar entry).
    "apps/events/templates/events/_storefront.html",
    "apps/events/feeds.py",
    # The blog, the CMS blocks, the footer and the form page, in `base` and in `ride`
    # (I18N.20 part 2); the editor-facing strings of these files join with part 3.
    "apps/cms/templates/cms/blog_author_page.html",
    "apps/cms/templates/cms/blog_index_page.html",
    "apps/cms/templates/cms/blog_post_page.html",
    "apps/cms/templates/cms/form_page.html",
    "apps/cms/templates/cms/form_page_landing.html",
    "apps/cms/templates/cms/blocks",
    "apps/cms/templates/cms/includes",
    "apps/cms/forms_pages.py",  # the refusals of the public form page (CMS.13)
    "apps/cms/blocks.py",  # only the launch countdown: its "Available now" (see ONLY_CLASSES)
    "themes/ride/templates/cms",
    "themes/base/templates/components",
    "themes/ride/templates/components",
    # The consumer-law storefront pages (I18N.20 part 3b, slice 1): consumer information,
    # withdrawal, privacy requests, notices, product safety, the legal pages and the ride
    # legal recap of the checkout. Informal customer register, legal terms of art per country.
    "apps/compliance/templates/compliance",
    "themes/ride/templates/compliance",
    "apps/cms/templates/cms/legal_page.html",
    "apps/cms/templates/cms/legal_page_version.html",
    "themes/ride/templates/orders/_legal_recap.html",
    "themes/ride/templates/orders/_legal_boxes.html",
    # Customer template overrides (I18N.20 part 3b, slice 2): the ride account and order
    # overrides, the consent banner, the garage saver and the image credits.
    "themes/ride/templates/accounts",
    "themes/ride/templates/orders",
    "themes/ride/templates/seo",
    "apps/fitment/templates/fitment/_garage_saver.html",
    # The "My garage" page of the customer area (FIT.5 2/3).
    "apps/fitment/templates/fitment/garage_account.html",
    "apps/fitment/templates/fitment/garage_delete.html",
    "apps/fitment/garage_views.py",
    # The header garage menu and the chooser (FIT.7 1/n).
    "apps/fitment/templates/fitment/garage_choose.html",
    "apps/fitment/views.py",
    "apps/fitment/garage_state.py",
    "themes/ride/templates/fitment",
    "apps/core/templates/core/components/image_credit.html",
    "apps/core/templates/core/components/image_credits_disclosure.html",
    # The remaining CMS page templates (I18N.20 part 3b, slice 3): landing and recruitment pages.
    "apps/cms/templates/cms/home_page.html",
    "apps/cms/templates/cms/standard_page.html",
    "apps/cms/templates/cms/term_landing_page.html",
    "apps/cms/templates/cms/vendor_landing_page.html",
    "apps/cms/templates/cms/vendor_recruitment_page.html",
    # The ride 404 and 500 pages (THM.30): the same msgids as base's.
    "themes/ride/templates/404.html",
    "themes/ride/templates/500.html",
    # Admin, editor and Manager-login templates (I18N.20 part 3b, slice 4): what staff and vendors
    # see in the brand merge, the image lists, the render monitor, the events moderation screens,
    # the ride Manager login and the error page. Formal register.
    "apps/catalog/templates/admin",
    "apps/catalog/templates/catalog/admin",
    "apps/cms/templates/cms/admin/image_review.html",
    "apps/cms/templates/cms/admin/content_snippet_confirm_delete.html",
    "apps/events/templates/events/admin",
    "themes/ride/templates/manager",
    "themes/ride/templates/layouts",
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
    "apps/orders/models.py",  # the state labels staff and the order pages show
    "apps/core/models.py",  # the image licence and author labels, the seeded "Own work"
    "apps/themes/models.py",  # the theme settings field labels
    "apps/orders/staff_drafts.py",  # the refusals staff read on a prepared cart (ORD.32)
    "apps/orders/tz.py",  # the business time zone refusal in the operator admin (ARC.27)
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
    "apps/cms/forms_pages.py": ("FormPage",),
    "apps/cms/blocks.py": ("LaunchCountdownBlock",),
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
            "apps/manager/templates/manager/shop/return_policy.html",
            "apps/manager/return_policy_views.py",
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


REVIEW_HEADER = "# review: sensitive entries reviewed (I18N.19)"


def _mark_entry_block(block: str) -> str:
    """`block` with a `# reviewed` translator comment, unless a sensitive entry has it already."""
    # A dummy header first: polib reads leading comments of a bare block as the file header.
    entry = polib.pofile('msgid ""\nmsgstr ""\n\n' + block + "\n")[0:1]
    if not entry or sensitive_group(entry[0]) is None or is_reviewed(entry[0]):
        return block
    lines = block.split("\n")
    at = next(i for i, line in enumerate(lines) if not line.startswith("# "))
    return "\n".join([*lines[:at], "# " + REVIEWED_MARK, *lines[at:]])


def mark_reviewed(lang: str) -> int:
    """Mark every sensitive entry of `lang` `# reviewed` and add the header line.

    A text edit: nothing is re-wrapped or re-ordered (the translator's act, after the review of
    `review-export`; never run it on an unreviewed catalog). Returns the entries newly marked.
    """
    path = _po_path(lang)
    head, _, rest = path.read_text(encoding="utf-8").partition("\n\n")
    blocks = [head, *rest.split("\n\n")]
    changed = [_mark_entry_block(b) for b in blocks[1:]]
    count = sum(1 for old, new in zip(blocks[1:], changed, strict=True) if old != new)
    if REVIEW_HEADER not in head:
        top, _, tail = head.partition("\nmsgid")
        head = f"{top}\n{REVIEW_HEADER}\nmsgid{tail}"
    path.write_text("\n\n".join([head, *changed]), encoding="utf-8")
    return count


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
    if command == "mark-reviewed":
        ap = argparse.ArgumentParser(prog="i18n_catalog.py mark-reviewed")
        ap.add_argument("lang", choices=LANGUAGES)
        print(f"{mark_reviewed(ap.parse_args(sys.argv[2:]).lang)} entries marked", file=sys.stderr)
        raise SystemExit(0)
    if command == "extract":
        extract()
    elif command == "compile":
        compile_all()
    elif command == "check":
        raise SystemExit(check())
    else:
        raise SystemExit(__doc__)
