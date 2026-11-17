"""ARC.36 coverage sweep: every signed-in route and every page type has help files.

The universe is the named routes of the signed-in spaces the ARC.32 sweeps cover (the Manager,
the second-factor pages, the checkout and customer account screens, a curated set of the
Wagtail and Django admin screens the roles use) and the concrete CMS and event page types. Each
needs `help/<lang>/<kind>/<stem>.md` in every ENABLED customer language (`DEFAULT_LANGUAGE_CODES`,
the I18N.19 gate: DE, FR and ES join when the gate lifts). Today's debt is counted in
`tests/help_coverage_baseline.txt`; the baseline only shrinks (ARC.37 empties it).

Also checked: an allow-list entry has a reason and names a real route; a help file never names a
route or page type that no longer exists; the files of one entry exist in all five languages
with the same placeholders; every file parses (an unknown placeholder fails); and a warning (not
a failure) when the route's view module or template changed after the file's last-checked date.
"""

import datetime as dt
import inspect
import re
import subprocess
import warnings
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path

from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver
from wagtail.models import Page, get_page_models

from apps.cms import engine, help_files
from mpmv.settings.languages import ALL_LANGUAGE_CODES, DEFAULT_LANGUAGE_CODES
from tests.integration.test_help_button_account_manager import MODAL_FRAGMENTS, NOT_PAGES

BASELINE = Path(__file__).resolve().parent.parent / "help_coverage_baseline.txt"
REPO = Path(__file__).resolve().parent.parent.parent
ENABLED_LANGUAGES = DEFAULT_LANGUAGE_CODES

# Routes of the signed-in spaces outside the Manager, by prefix or exact name.
SPACE_PREFIXES = ("manager:", "mfa:", "orders:")
SPACE_NAMES = (
    "customer_orders",
    "customer_order",
    "privacy_account",
    "privacy_requests",
    "withdrawal",
    "withdrawal_confirm",
    "vendors:apply",
    # Wagtail and Django admin screens the roles use (ARC.37 part 4 widens this list).
    "wagtailadmin_home",
    "wagtailadmin_explore_root",
    "wagtailadmin_explore",
    "wagtailadmin_pages:add",
    "wagtailadmin_pages:edit",
    "wagtailsnippets_cms_helpentry:list",
    "wagtailimages:index",
    "wagtaildocs:index",
    "admin:index",
)

# Routes that need no help, each with its reason.
ALLOWED: dict[str, str] = {
    **{
        name: "not a page: a redirect, POST-only or file route (the account/Manager sweep)"
        for name in NOT_PAGES
        if name not in MODAL_FRAGMENTS
    },
    **{
        name: "a modal fragment of Wagtail's chooser protocol, no page layout"
        for name in MODAL_FRAGMENTS
    },
    "mfa:passkey_auth": "JSON endpoint for the browser's passkey prompt",
    "mfa:passkey_auth_options": "JSON endpoint for the browser's passkey prompt",
    "mfa:passkey_register": "JSON endpoint for the browser's passkey prompt",
    "mfa:passkey_register_options": "JSON endpoint for the browser's passkey prompt",
    "mfa:remove_device": "POST only, redirects to the factors page",
    "orders:cart_add": "POST only, redirects to the cart",
    "orders:cart_mini": "a fragment of the header, not a screen",
}


def _named_routes() -> Iterator[tuple[str, Callable[..., object]]]:
    def walk(patterns: list[object], ns: str) -> Iterator[tuple[str, Callable[..., object]]]:
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                prefix = f"{ns}{pattern.namespace}:" if pattern.namespace else ns
                yield from walk(list(pattern.url_patterns), prefix)
            elif isinstance(pattern, URLPattern) and pattern.name:
                yield ns + pattern.name, pattern.callback

    yield from walk(list(get_resolver().url_patterns), "")


ROUTES: dict[str, Callable[..., object]] = dict(_named_routes())


def route_universe() -> set[str]:
    return {
        name for name in ROUTES if name.startswith(SPACE_PREFIXES) or name in SPACE_NAMES
    } - ALLOWED.keys()


def page_types() -> set[str]:
    return {
        model._meta.label
        for model in get_page_models()
        if model is not Page
        and not model._meta.abstract
        and model._meta.app_label in {"cms", "events"}
    }


def _read_baseline() -> tuple[int, list[str]]:
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    count = int(lines[0].removeprefix("# count:").strip())
    return count, [line for line in lines if line and not line.startswith("#")]


def _files() -> list[help_files.HelpFile]:
    return help_files.load_all()  # raises on a bad file or an unknown placeholder


def _have() -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for file in _files():
        found[(file.kind, file.key)].add(file.language)
    return found


def missing_keys() -> set[str]:
    have = _have()
    wanted = [(engine.HELP_ROUTE, n) for n in route_universe()]
    wanted += [(engine.HELP_PAGE_TYPE, t) for t in page_types()]
    return {
        f"{kind} {key}"
        for kind, key in wanted
        if not set(ENABLED_LANGUAGES) <= have.get((kind, key), set())
    }


def test_the_universe_covers_the_signed_in_spaces() -> None:
    routes = route_universe()
    assert any(r.startswith("manager:shop:") for r in routes)
    assert any(r.startswith("manager:platform:") for r in routes)
    assert any(r.startswith("mfa:") for r in routes)
    assert any(r.startswith("orders:") for r in routes)
    assert "cms.BlogPostPage" in page_types()
    assert "events.EventPage" in page_types()


def test_every_allow_list_entry_has_a_reason_and_names_a_real_route() -> None:
    for name, reason in ALLOWED.items():
        assert reason.strip(), f"{name}: an allow-list entry needs a reason"
        assert name in ROUTES, f"{name}: allow-listed but no such route"
    for name in SPACE_NAMES:
        assert name in ROUTES, f"{name}: listed in SPACE_NAMES but no such route"


def test_no_new_route_or_page_type_is_missing_its_help_files() -> None:
    _count, baseline = _read_baseline()
    new = sorted(missing_keys() - set(baseline))
    assert not new, (
        "no help file in every enabled language ("
        f"{', '.join(ENABLED_LANGUAGES)}; see docs/dev/help-content.md): {new}"
    )


def test_the_baseline_only_shrinks() -> None:
    count, baseline = _read_baseline()
    covered = sorted(set(baseline) - missing_keys())
    assert not covered, f"these now have help files: delete them from the baseline: {covered}"
    assert count == len(baseline), f"the first line says {count}, the file has {len(baseline)}"
    print(f"help coverage baseline: {len(baseline)} keys still without help")


def test_a_help_file_never_names_a_route_or_page_type_that_is_gone() -> None:
    stale = sorted(
        str(file.path.relative_to(help_files.HELP_ROOT))
        for file in _files()
        if (file.kind == engine.HELP_ROUTE and file.key not in ROUTES)
        or (file.kind == engine.HELP_PAGE_TYPE and file.key not in page_types())
    )
    assert not stale, f"help files for a route or page type that no longer exists: {stale}"


def test_every_entry_has_the_same_files_in_all_five_languages() -> None:
    incomplete = {
        f"{kind} {key or 'general'}": sorted(set(ALL_LANGUAGE_CODES) - langs)
        for (kind, key), langs in _have().items()
        if langs != set(ALL_LANGUAGE_CODES)
    }
    assert not incomplete, f"missing languages per entry: {incomplete}"


def test_the_files_of_one_entry_use_the_same_placeholders() -> None:
    names: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(dict)
    for file in _files():
        names[(file.kind, file.key)][file.language] = help_files.placeholders_in(
            f"{file.title}\n{file.markdown}"
        )
    differing = [key for key, per in names.items() if len({frozenset(p) for p in per.values()}) > 1]
    assert not differing, f"placeholders differ between languages: {differing}"


def test_the_general_entry_exists_in_every_language() -> None:
    assert _have()[(engine.HELP_GENERAL, "")] == set(ALL_LANGUAGE_CODES)


# --- Last-checked: a warning, never a failure


def _last_change(path: Path) -> dt.date | None:
    try:
        out = subprocess.run(  # noqa: S603 (fixed git command, no user input)
            ["git", "log", "-1", "--format=%cs", "--", str(path)],  # noqa: S607 (git from PATH, as in CI)
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        ).stdout.strip()
        if out:
            return dt.date.fromisoformat(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return dt.date.fromtimestamp(path.stat().st_mtime) if path.exists() else None


def _sources(route: str) -> list[Path]:
    callback = ROUTES.get(route)
    if callback is None:
        return []
    paths: list[Path] = []
    try:
        paths.append(Path(inspect.getfile(callback)))
        for name in re.findall(r"[\"']([\w/.-]+\.html)[\"']", inspect.getsource(callback)):
            try:
                origin = getattr(get_template(name), "origin", None)
            except TemplateDoesNotExist:
                continue
            if origin is not None and origin.name:
                paths.append(Path(origin.name))
    except (OSError, TypeError):
        pass
    return paths


def test_a_stale_last_checked_date_is_only_a_warning() -> None:
    stale: list[str] = []
    for file in _files():
        if file.kind != engine.HELP_ROUTE or file.language != "en":
            continue
        for source in _sources(file.key):
            changed = _last_change(source)
            if changed is not None and changed > file.last_checked:
                stale.append(
                    f"{file.key}: {source.name} changed {changed}, last-checked {file.last_checked}"
                )
                break
    for line in stale:
        warnings.warn(f"help may be out of date: {line}", stacklevel=1)
    if stale:
        print(f"{len(stale)} help files may be out of date (see warnings)")
