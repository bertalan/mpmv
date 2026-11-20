"""ReDoS sweep helpers (SEC.5, attack scenarios: resource abuse).

`collect_patterns()` finds every regular expression the code under `apps/` can run:

- literal patterns passed to `re.compile/match/search/fullmatch/sub/subn/split/findall/finditer`,
  `RegexValidator` and `re_path` (AST scan of every module outside `tests/` and `migrations/`);
- compiled patterns held at module level, also inside dicts, lists, tuples and class bodies
  (runtime scan: catches patterns built with f-strings or comprehensions);
- `RegexValidator`s on the fields of our models;
- every URL pattern the root URLconf resolves (ours and the third-party routes it includes).

A call site whose pattern is neither a literal nor a module-level compiled pattern is reported as
unresolved, so the sweep cannot silently miss a dynamic regex.

`sweep()` runs each pattern against pathological inputs (a pumped character, pair or literal run
followed by a character that forces a failed match) in a separate process, one operation at a
time with a hard kill, so a catastrophic pattern fails the test instead of hanging it.
The worker side imports neither Django nor the code under test.
"""

import ast
import importlib
import multiprocessing
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / "apps"

# The method a call uses decides the attack: an unanchored scan retries at every offset.
OPERATION_OF = {
    "match": "match",
    "fullmatch": "fullmatch",
    **dict.fromkeys(("search", "sub", "subn", "split", "findall", "finditer"), "search"),
}
# When the use of a compiled pattern is not visible: try both ends of the spectrum.
SCAN = frozenset({"search", "fullmatch"})
RE_FUNCTIONS = frozenset({"compile", *OPERATION_OF})
# Position of the `flags` argument per function (0-based), for literal flag expressions.
_FLAGS_POSITION = {
    "compile": 1,
    **dict.fromkeys(("match", "search", "fullmatch", "findall", "finditer"), 2),
    "split": 3,
    "sub": 4,
    "subn": 4,
}
_FLAG_NAMES = {
    name: int(getattr(re, name))
    for name in ("A", "ASCII", "I", "IGNORECASE", "M", "MULTILINE", "S", "DOTALL", "X", "VERBOSE")
}

# A character that no pattern under test expects at the end of a pumped input: it forces the
# engine to backtrack through every way of matching the pumped part.
FAIL_SUFFIXES = ("\x00", "!", "\n")
# Representatives of the character classes patterns use, plus delimiters attackers repeat.
REPRESENTATIVES = "aA0 \t-_./:@<>\"'=#%&()[]{},;\\é*+?|$^"


@dataclass(frozen=True)
class Found:
    pattern: str | bytes
    flags: int
    where: str  # file:line, or urlconf:<route>
    # How the code runs it: "search" (also sub, split, findall...), "match" or "fullmatch".
    operations: frozenset[str] = SCAN

    @property
    def key(self) -> tuple[str | bytes, int]:
        return (self.pattern, self.flags)


@dataclass(frozen=True)
class Unresolved:
    where: str
    source: str


# --- Collection


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def source_files() -> Iterator[Path]:
    for path in sorted(APPS.rglob("*.py")):
        parts = path.relative_to(APPS).parts
        if "tests" in parts or "migrations" in parts:
            continue
        yield path


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "literal"` (also concatenations of literals)."""
    names: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None:
            continue
        resolved = _literal(value, names)
        if resolved is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names[target.id] = resolved
    return names


def _literal(node: ast.expr, names: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left, names), _literal(node.right, names)
        if left is not None and right is not None:
            return left + right
    return None


def _flags(node: ast.expr | None) -> int | None:
    if node is None:
        return 0
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "re"
        and node.attr in _FLAG_NAMES
    ):
        return _FLAG_NAMES[node.attr]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _flags(node.left), _flags(node.right)
        if left is not None and right is not None:
            return left | right
    if isinstance(node, ast.Constant) and node.value == 0:
        return 0
    return None


def _call_kind(call: ast.Call) -> str | None:
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
        and func.attr in RE_FUNCTIONS
    ):
        return func.attr
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in {"RegexValidator", "re_path"}:
        return name
    return None


def _arguments(call: ast.Call, kind: str) -> tuple[ast.expr | None, ast.expr | None]:
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    pattern = call.args[0] if call.args else keywords.get("regex", keywords.get("pattern"))
    if kind in _FLAGS_POSITION:
        position = _FLAGS_POSITION[kind]
        flags = call.args[position] if len(call.args) > position else keywords.get("flags")
    elif kind == "RegexValidator":
        flags = call.args[4] if len(call.args) > 4 else keywords.get("flags")
    else:
        flags = None
    return pattern, flags


def static_scan() -> tuple[list[Found], list[Unresolved]]:
    """Literal patterns at every regex call site under apps/, and the sites that are not."""
    found: list[Found] = []
    unresolved: list[Unresolved] = []
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = _string_constants(tree)
        relative = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kind = _call_kind(node)
            if kind is None:
                continue
            where = f"{relative}:{node.lineno}"
            pattern_node, flags_node = _arguments(node, kind)
            pattern = None if pattern_node is None else _literal(pattern_node, constants)
            flags = _flags(flags_node)
            if pattern is None or flags is None:
                unresolved.append(Unresolved(where, ast.unparse(node)[:120]))
                continue
            # re.compile(): the use is unknown here; the runtime scan or SCAN fills it in.
            operations = frozenset({OPERATION_OF[kind]}) if kind in OPERATION_OF else frozenset()
            found.append(Found(pattern, flags, where, operations))
    return found, unresolved


def _patterns_in(value: Any, depth: int = 0) -> Iterator[re.Pattern[Any]]:
    if isinstance(value, re.Pattern):
        yield value
    elif depth < 2 and isinstance(value, dict):
        for item in value.values():
            yield from _patterns_in(item, depth + 1)
    elif depth < 2 and isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from _patterns_in(item, depth + 1)


@dataclass(frozen=True)
class _Definition:
    line: int
    end_line: int


def _definitions(tree: ast.Module) -> dict[str, _Definition]:
    """Where each module-level name is assigned (or class defined), with the statement's span."""
    lines: dict[str, _Definition] = {}
    for node in tree.body:
        span = _Definition(node.lineno, node.end_lineno or node.lineno)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    lines[target.id] = span
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            lines[node.target.id] = span
        elif isinstance(node, ast.ClassDef):
            lines[node.name] = span
    return lines


def _uses(tree: ast.Module) -> dict[str, set[str]]:
    """The regex methods called on each name in a module: `NAME.search(...)`,
    `self.NAME.fullmatch(...)`, `NAME[key].match(...)`."""
    uses: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr in OPERATION_OF):
            continue
        holder = node.value
        if isinstance(holder, ast.Subscript):
            holder = holder.value
        name = holder.id if isinstance(holder, ast.Name) else getattr(holder, "attr", None)
        if name is not None:
            uses.setdefault(name, set()).add(OPERATION_OF[node.attr])
    return uses


def runtime_scan() -> tuple[list[Found], set[str]]:
    """Compiled patterns held by modules under apps/ (module level and class bodies).

    Also returns every `file:line` inside the statements that hold them: a dynamic call site
    there (an f-string, a comprehension) is covered by the pattern it built.
    """
    found: list[Found] = []
    covered: set[str] = set()
    for path in source_files():
        module = importlib.import_module(_module_name(path))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definitions = _definitions(tree)
        uses = _uses(tree)
        relative = path.relative_to(ROOT)
        for name, value in vars(module).items():
            if name not in definitions:
                continue  # imported, not defined here
            definition = definitions[name]
            # A class: its compiled class attributes, used as self.NAME or cls.NAME.
            members = list(vars(value).items()) if isinstance(value, type) else [(name, value)]
            patterns = []
            for member, item in members:
                operations = frozenset(uses.get(member, ())) or SCAN
                for pattern in _patterns_in(item):
                    patterns.append(pattern)
                    where = f"{relative}:{definition.line}"
                    found.append(Found(pattern.pattern, pattern.flags, where, operations))
            if patterns:
                covered.update(
                    f"{relative}:{line}" for line in range(definition.line, definition.end_line + 1)
                )
    return found, covered


def model_validator_scan() -> list[Found]:
    from django.apps import apps
    from django.core.validators import RegexValidator

    found: list[Found] = []
    for model in apps.get_models():
        if not model.__module__.startswith("apps."):
            continue
        for field in model._meta.get_fields():
            for validator in getattr(field, "validators", ()):
                if isinstance(validator, RegexValidator):
                    regex = validator.regex  # a lazy re.Pattern
                    assert isinstance(regex, re.Pattern)
                    where = f"{model.__module__}.{model.__name__}.{field.name}"
                    # RegexValidator runs regex.search().
                    found.append(Found(regex.pattern, regex.flags, where, frozenset({"search"})))
    return found


def url_scan() -> tuple[list[Found], set[str]]:
    """Every URL pattern of the root URLconf, and the URLconf modules it includes."""
    from django.urls import URLResolver, get_resolver

    found: list[Found] = []
    modules: set[str] = set()

    def walk(resolver: URLResolver, prefix: str) -> None:
        name = getattr(resolver.urlconf_module, "__name__", None)
        if name:
            modules.add(name)
        for entry in resolver.url_patterns:
            route = f"{prefix}{entry.pattern}"
            regex = entry.pattern.regex
            target = getattr(entry, "lookup_str", "")
            where = f"urlconf:{route} {target}".strip()
            # The resolver runs regex.search() (RegexPattern) or regex.fullmatch() (RoutePattern).
            found.append(Found(regex.pattern, regex.flags, where))
            if isinstance(entry, URLResolver):
                walk(entry, route)

    walk(get_resolver(), "/")
    return found, modules


def collect_patterns() -> tuple[list[Found], list[Unresolved]]:
    """Every distinct (pattern, flags) with its first location, and unresolved call sites."""
    static, unresolved = static_scan()
    runtime, covered = runtime_scan()
    urls, urlconfs = url_scan()
    urlconf_files = {
        str(Path(*name.split(".")).with_suffix(".py"))
        for name in urlconfs
        if name.startswith("apps.")
    }
    unresolved = [
        site
        for site in unresolved
        if site.where not in covered
        # A dynamic re_path() in an included URLconf is swept through the resolver.
        and not (site.source.startswith("re_path(") and site.where.split(":")[0] in urlconf_files)
    ]
    unique: dict[tuple[str | bytes, int], Found] = {}
    for found in [*static, *runtime, *model_validator_scan(), *urls]:
        found = replace(found, flags=_effective_flags(found))
        first = unique.setdefault(found.key, found)
        if first is not found:  # the same regex elsewhere: sweep every way it is used
            unique[found.key] = replace(first, operations=first.operations | found.operations)
    patterns = [replace(found, operations=found.operations or SCAN) for found in unique.values()]
    return patterns, unresolved


def _effective_flags(found: Found) -> int:
    """The flags of the compiled pattern (str patterns get re.UNICODE implicitly)."""
    try:
        return re.compile(found.pattern, found.flags).flags
    except re.error:
        return found.flags


# --- Inputs


def _literal_runs(pattern: str, flags: int) -> tuple[set[str], list[str]]:
    """Characters and multi-character literal runs that appear in the pattern."""
    from re import _parser  # type: ignore[attr-defined]

    chars: set[str] = set()
    runs: list[str] = []

    def walk(items: Any) -> None:
        run = ""
        for op, value in items:
            name = str(op)
            if name == "LITERAL":
                chars.add(chr(value))
                run += chr(value)
                continue
            if len(run) > 1:
                runs.append(run)
            run = ""
            if name in {"SUBPATTERN"}:
                walk(value[-1])
            elif name in {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}:
                walk(value[2])
            elif name == "BRANCH":
                for branch in value[1]:
                    walk(branch)
            elif name in {"ASSERT", "ASSERT_NOT"}:
                walk(value[1])
            elif name == "IN":
                for in_op, in_value in value:
                    if str(in_op) == "LITERAL":
                        chars.add(chr(in_value))
                    elif str(in_op) == "RANGE":
                        chars.add(chr(in_value[0]))
                        chars.add(chr(in_value[1]))
        if len(run) > 1:
            runs.append(run)

    walk(_parser.parse(pattern, flags))
    return chars, runs


def attack_inputs(pattern: str, flags: int, size: int) -> list[str]:
    """Pumped inputs of about `size` characters, each ending in a failing suffix."""
    chars, runs = _literal_runs(pattern, flags)
    alphabet = sorted(set(REPRESENTATIVES) | chars)
    inputs: list[str] = []
    for char in alphabet:
        pumped = char * size
        inputs.append(pumped)
        inputs.extend(pumped + suffix for suffix in FAIL_SUFFIXES)
        inputs.append("a" + pumped + "\x00")
    pair_chars = sorted(chars)[:12] + list("a0 -.")
    for first in pair_chars:
        for second in pair_chars:
            if first < second:
                inputs.append((first + second) * (size // 2) + "\x00")
    for run in runs[:20]:
        inputs.append(run * max(1, size // len(run)) + "\x00")
        # An unclosed construct: the opening run repeated, e.g. "url(" or "<a ".
        inputs.append(run[0] * size)
    return list(dict.fromkeys(inputs))


# --- Sweep in a worker process


Job = tuple[int, str | bytes, int, tuple[str, ...]]


def _worker(connection: Connection, jobs: list[Job], size: int) -> None:
    for index, pattern, flags, operation_names in jobs:
        try:
            compiled = re.compile(pattern, flags)
        except re.error:
            connection.send(("error", index, 0.0))
            continue
        if isinstance(pattern, bytes):
            connection.send(("done", index, 0.0))
            continue
        worst = 0.0
        for text in attack_inputs(pattern, flags, size):
            for operation in [getattr(compiled, name) for name in operation_names]:
                connection.send(("op", index, 0.0))
                started = time.perf_counter()
                operation(text)
                worst = max(worst, time.perf_counter() - started)
        connection.send(("done", index, worst))
    connection.send(("finished", -1, 0.0))


@dataclass(frozen=True)
class Slow:
    found: Found
    seconds: float  # the worst single operation; the hard limit when the worker was killed
    killed: bool


def sweep(patterns: list[Found], *, size: int, budget: float, hard_limit: float) -> list[Slow]:
    """Run every pattern on attack inputs of `size` characters; return those over `budget`.

    One operation that runs past `hard_limit` seconds kills the worker: the pattern is reported
    and the sweep goes on with the next one.
    """
    context = multiprocessing.get_context("spawn")
    slow: list[Slow] = []
    remaining = list(range(len(patterns)))
    while remaining:
        jobs: list[Job] = [
            (i, patterns[i].pattern, patterns[i].flags, tuple(sorted(patterns[i].operations)))
            for i in remaining
        ]
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=_worker, args=(child, jobs, size), daemon=True)
        process.start()
        child.close()
        current = remaining[0]
        done: set[int] = set()
        killed = False
        # The first message waits for the interpreter to start and import this module.
        wait = 60.0
        while True:
            if not parent.poll(wait):
                killed = True
                break
            wait = hard_limit
            try:
                kind, index, seconds = parent.recv()
            except EOFError:  # the worker died (e.g. out of memory): blame the current pattern
                killed = True
                break
            if kind == "finished":
                break
            current = index
            if kind in {"done", "error"}:
                done.add(index)
                if seconds > budget:
                    slow.append(Slow(patterns[index], seconds, killed=False))
        if killed:
            process.kill()
            done.add(current)
            slow.append(Slow(patterns[current], hard_limit, killed=True))
        process.join()
        parent.close()
        remaining = [i for i in remaining if i not in done]
    return slow


def report(slow: list[Slow]) -> str:
    lines = [
        f"{entry.found.where}: {entry.found.pattern!r} (flags={entry.found.flags}, "
        f"{'/'.join(sorted(entry.found.operations))}) "
        + ("killed after" if entry.killed else "took")
        + f" {entry.seconds:.2f}s on one input"
        for entry in sorted(slow, key=lambda entry: entry.found.where)
    ]
    return "Regular expressions over the time budget (ReDoS):\n" + "\n".join(lines)
