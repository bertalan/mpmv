"""ARC.36 coverage sweep: every signed-in route and every page type has help files.

The universe is the named routes of the signed-in spaces the ARC.32 sweeps cover (the Manager,
the second-factor pages, the checkout and customer account screens, a curated set of the
Wagtail and Django admin screens the roles use) and the concrete CMS and event page types. Each
needs `help/<lang>/<kind>/<stem>.md` in every ENABLED language (`settings.LANGUAGES`, the
effective set after the I18N.19 gate, so DE, FR and ES join when the gate lifts). Today's debt
is counted in `tests/help_coverage_baseline.txt`; the baseline only shrinks (ARC.37 empties it).

Scope, on purpose: the universe names only the Manager, the second-factor, the orders and the
customer-account routes, a short list of Wagtail and Django admin screens (`SPACE_NAMES`) and the
concrete CMS and event page types. The rest of the admin is deferred to ARC.37 part 4 because
(1) those are Wagtail's and Django's own screens (page chooser variants, other snippet models,
users, groups, reports, workflows, the django-admin models), each needing its own help text and
a decision about what a role may see there; (2) the admin help button is one generic entry per
screen family, not per route name, so a per-route sweep over them would count noise; and
(3) ARC.37 part 4 owns the admin help texts and widens `SPACE_NAMES` with them. Until then that
debt is recorded here and in docs/dev/help-content.md, not invisible.

The checks are pure functions over a help root, the routes and the languages, so the tests at
the end show each guard failing on a temporary file tree (a missing file, a stale key,
mismatched keys across languages, an unknown placeholder, a baseline entry that has files).

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
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pytest
from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver
from wagtail.models import Page, get_page_models

from apps.cms import engine, help_files
from mpmv.settings.languages import ALL_LANGUAGE_CODES
from tests.help_routes import ALLOWED

BASELINE = Path(__file__).resolve().parent.parent / "help_coverage_baseline.txt"
REPO = Path(__file__).resolve().parent.parent.parent


def enabled_languages() -> tuple[str, ...]:
    """The languages every entry must exist in: the effective `settings.LANGUAGES` (the I18N.19
    gate decides it, through `MPMV_LANGUAGES`), never a literal list."""
    return tuple(code for code, _name in settings.LANGUAGES)


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


def _read_baseline(path: Path = BASELINE) -> tuple[int, list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    count = int(lines[0].removeprefix("# count:").strip())
    return count, [line for line in lines if line and not line.startswith("#")]


def _files(root: Path = help_files.HELP_ROOT) -> list[help_files.HelpFile]:
    return help_files.load_all(root)  # raises on a bad file or an unknown placeholder


def _have(root: Path = help_files.HELP_ROOT) -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for file in _files(root):
        found[(file.kind, file.key)].add(file.language)
    return found


def wanted_keys(routes: Iterable[str], types: Iterable[str]) -> list[tuple[str, str]]:
    return [(engine.HELP_ROUTE, n) for n in routes] + [(engine.HELP_PAGE_TYPE, t) for t in types]


def missing_keys(
    root: Path = help_files.HELP_ROOT,
    routes: Iterable[str] | None = None,
    types: Iterable[str] | None = None,
    languages: Iterable[str] | None = None,
) -> set[str]:
    """`kind key` of every wanted route or page type lacking a file in an enabled language."""
    have = _have(root)
    need = set(enabled_languages() if languages is None else languages)
    wanted = wanted_keys(
        route_universe() if routes is None else routes, page_types() if types is None else types
    )
    return {f"{kind} {key}" for kind, key in wanted if not need <= have.get((kind, key), set())}


def stale_files(
    root: Path = help_files.HELP_ROOT,
    routes: Iterable[str] | None = None,
    types: Iterable[str] | None = None,
) -> list[str]:
    """Files naming a route or page type that no longer exists. `routes` are all named routes."""
    known_routes = set(ROUTES if routes is None else routes)
    known_types = page_types() if types is None else set(types)
    return sorted(
        str(file.path.relative_to(root))
        for file in _files(root)
        if (file.kind == engine.HELP_ROUTE and file.key not in known_routes)
        or (file.kind == engine.HELP_PAGE_TYPE and file.key not in known_types)
    )


def incomplete_entries(
    root: Path = help_files.HELP_ROOT, languages: Iterable[str] = ALL_LANGUAGE_CODES
) -> dict[str, list[str]]:
    """Entries whose files do not exist in every one of `languages`."""
    every = set(languages)
    return {
        f"{kind} {key or 'general'}": sorted(every - langs)
        for (kind, key), langs in _have(root).items()
        if langs != every
    }


def placeholder_mismatches(root: Path = help_files.HELP_ROOT) -> list[tuple[str, str]]:
    names: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(dict)
    for file in _files(root):
        names[(file.kind, file.key)][file.language] = help_files.placeholders_in(
            f"{file.title}\n{file.markdown}"
        )
    return [key for key, per in names.items() if len({frozenset(p) for p in per.values()}) > 1]


def baseline_problems(
    baseline: list[str], missing: set[str], wanted: set[str]
) -> tuple[list[str], list[str]]:
    """`(covered, unknown)`: baseline lines that now have their files (delete them), and lines
    naming nothing that is wanted any more (also delete them: the baseline only shrinks)."""
    return sorted((set(baseline) & wanted) - missing), sorted(set(baseline) - wanted)


def _baseline_on_main() -> list[str] | None:
    """The baseline keys as `origin/main` has them, or None when git or the ref is not there."""
    try:
        result = subprocess.run(
            ["git", "show", "origin/main:tests/help_coverage_baseline.txt"],  # noqa: S607 (git from PATH, as in CI)
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [ln for ln in result.stdout.splitlines() if ln and not ln.startswith("#")]


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


def test_the_enabled_languages_follow_the_effective_settings() -> None:
    assert enabled_languages() == tuple(code for code, _ in settings.LANGUAGES)
    assert set(enabled_languages()) <= set(ALL_LANGUAGE_CODES)
    assert enabled_languages()[0] == settings.LANGUAGE_CODE


def test_no_new_route_or_page_type_is_missing_its_help_files() -> None:
    _count, baseline = _read_baseline()
    new = sorted(missing_keys() - set(baseline))
    assert not new, (
        "no help file in every enabled language ("
        f"{', '.join(enabled_languages())}; see docs/dev/help-content.md): {new}"
    )


def test_the_baseline_only_shrinks() -> None:
    """Local gate: no line that has files or names a gone route; the count line is honest. The
    comparison with the version on `origin/main` is a warning below (git may be absent in CI)."""
    count, baseline = _read_baseline()
    wanted = {f"{kind} {key}" for kind, key in wanted_keys(route_universe(), page_types())}
    covered, unknown = baseline_problems(baseline, missing_keys(), wanted)
    assert not covered, f"these now have help files: delete them from the baseline: {covered}"
    assert not unknown, f"these name no wanted route or page type: delete them: {unknown}"
    assert count == len(baseline), f"the first line says {count}, the file has {len(baseline)}"
    assert len(baseline) == len(set(baseline)), "the baseline lists a key twice"
    print(f"help coverage baseline: {len(baseline)} keys still without help")


def test_the_baseline_has_not_grown_since_main_is_only_a_warning() -> None:
    """Adding a key to the baseline is not allowed (reviewers reject it). Where `origin/main` is
    known, a key it does not list warns; review is the gate, this makes it visible."""
    _count, baseline = _read_baseline()
    on_main = _baseline_on_main()
    if on_main is None:
        return
    grown = sorted(set(baseline) - set(on_main))
    for key in grown:
        warnings.warn(f"help baseline grew: {key} is not on origin/main", stacklevel=1)
    if grown:
        print(f"the help baseline grew by {len(grown)} keys (see warnings): not allowed")


def test_a_help_file_never_names_a_route_or_page_type_that_is_gone() -> None:
    stale = stale_files()
    assert not stale, f"help files for a route or page type that no longer exists: {stale}"


def test_every_entry_has_the_same_files_in_all_five_languages() -> None:
    incomplete = incomplete_entries()
    assert not incomplete, f"missing languages per entry: {incomplete}"


def test_the_files_of_one_entry_use_the_same_placeholders() -> None:
    differing = placeholder_mismatches()
    assert not differing, f"placeholders differ between languages: {differing}"


def test_the_general_entry_exists_in_every_language() -> None:
    assert _have()[(engine.HELP_GENERAL, "")] == set(ALL_LANGUAGE_CODES)


# --- Each guard fails when it should (a temporary tree, injected routes and languages)


def _write(
    root: Path, lang: str, kind: str, stem: str, body: str = "Body.", title: str = "T"
) -> None:
    path = root / lang / kind / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\nlast-checked: 2026-11-17\n\n{body}\n", encoding="utf-8")


def test_a_new_route_is_missing_until_its_files_exist(tmp_path: Path) -> None:
    routes, langs = ["manager:shop:new"], ("en", "it")
    assert missing_keys(tmp_path, routes, [], langs) == {"route manager:shop:new"}
    _write(tmp_path, "en", "route", "manager__shop__new")
    assert missing_keys(tmp_path, routes, [], langs) == {"route manager:shop:new"}  # one language
    _write(tmp_path, "it", "route", "manager__shop__new")
    assert missing_keys(tmp_path, routes, [], langs) == set()


def test_a_page_type_without_files_is_missing(tmp_path: Path) -> None:
    assert missing_keys(tmp_path, [], ["cms.NewPage"], ("en",)) == {"page_type cms.NewPage"}
    _write(tmp_path, "en", "page_type", "cms.NewPage")
    assert missing_keys(tmp_path, [], ["cms.NewPage"], ("en",)) == set()


def test_a_newly_enabled_language_makes_covered_entries_missing_again(tmp_path: Path) -> None:
    _write(tmp_path, "en", "route", "a__b")
    _write(tmp_path, "it", "route", "a__b")
    assert missing_keys(tmp_path, ["a:b"], [], ("en", "it")) == set()
    assert missing_keys(tmp_path, ["a:b"], [], ("en", "it", "de")) == {"route a:b"}


def test_a_file_for_a_removed_route_or_page_type_is_stale(tmp_path: Path) -> None:
    _write(tmp_path, "en", "route", "a__b")
    _write(tmp_path, "en", "route", "gone__route")
    _write(tmp_path, "en", "page_type", "cms.GonePage")
    _write(tmp_path, "en", "general", "general")
    stale = stale_files(tmp_path, ["a:b"], ["cms.HomePage"])
    assert stale == ["en/page_type/cms.GonePage.md", "en/route/gone__route.md"]


def test_mismatched_keys_across_languages_are_reported(tmp_path: Path) -> None:
    _write(tmp_path, "en", "route", "a__b")
    _write(tmp_path, "it", "route", "a__b")
    _write(tmp_path, "en", "route", "only__en")
    found = incomplete_entries(tmp_path, ("en", "it"))
    assert found == {"route only:en": ["it"]}


def test_different_placeholders_between_languages_are_reported(tmp_path: Path) -> None:
    _write(tmp_path, "en", "route", "a__b", "Mail {{ operator.support_email }}")
    _write(tmp_path, "it", "route", "a__b", "Scrivi a {{ operator.compliance_email }}")
    assert placeholder_mismatches(tmp_path) == [("route", "a:b")]
    _write(tmp_path, "it", "route", "a__b", "Scrivi a {{ operator.support_email }}")
    assert placeholder_mismatches(tmp_path) == []


def test_an_unknown_placeholder_fails_the_load(tmp_path: Path) -> None:
    _write(tmp_path, "en", "route", "a__b", "Hello {{ operator.nope }}")
    with pytest.raises(help_files.HelpFileError, match="unknown placeholder"):
        missing_keys(tmp_path, ["a:b"], [], ("en",))


def test_a_baseline_entry_that_now_has_files_or_names_nothing_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "en", "route", "a__b")
    wanted = {"route a:b", "route c:d"}
    missing = missing_keys(tmp_path, ["a:b", "c:d"], [], ("en",))
    covered, unknown = baseline_problems(["route a:b", "route c:d", "route x:y"], missing, wanted)
    assert covered == ["route a:b"]
    assert unknown == ["route x:y"]
    assert baseline_problems(["route c:d"], missing, wanted) == ([], [])


def test_a_baseline_file_with_a_wrong_count_is_read_as_it_says(tmp_path: Path) -> None:
    path = tmp_path / "baseline.txt"
    path.write_text("# count: 3\n# note\nroute a:b\nroute c:d\n", encoding="utf-8")
    count, keys = _read_baseline(path)
    assert (count, keys) == (3, ["route a:b", "route c:d"])
    assert count != len(keys)  # the sweep's own test would fail on this file


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
