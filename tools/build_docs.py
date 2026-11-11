# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown>=3.5"]
# ///
"""Build the generated spec artifacts from docs/spec/*.md.

Outputs:
  docs/spec/90-backlog.md   generated backlog (committed)
  docs/spec/backlog.csv     same backlog for Jira/Linear/GitHub Projects (committed)
  build/mpmv-spec.html  single-page HTML of the whole spec (not committed)

Usage:
  uv run tools/build_docs.py           # regenerate everything
  uv run tools/build_docs.py --check   # exit 1 if committed artifacts are stale (CI / pre-commit)

Single source of truth for tasks: the tables
"| ID | Micro-task | Pri | Hours | Depends on | Done when |" in the domain documents.
The script validates unique IDs, existing dependencies and the absence of cycles.
"""

from __future__ import annotations

import csv
import html
import io
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "spec"
BUILD = ROOT / "build"
HTML_OUT = BUILD / "mpmv-spec.html"
PROGRESS = ROOT / "docs" / "progress.md"
PROGRESS_ROW = re.compile(r"^\|\s*([A-Z0-9]+\.\d+)\s*\|\s*(done|in review|in progress)\s*\|")
GENERATED = {"90-backlog.md"}

MACRO = {
    "ARC": "Architecture and foundations",
    "CORE": "Identity, permissions, API, backoffice",
    "CAT": "Product catalog",
    "VEN": "Vendors, commissions, ledger",
    "TAX": "Tax engine",
    "SHP": "Shipping and quotes",
    "ORD": "Cart, orders, returns",
    "PAY": "Payments (Stripe Connect)",
    "ING": "Supplier import and pipelines",
    "I18N": "Multilingual",
    "CMS": "Wagtail content",
    "EVT": "Events, feeds and maps",
    "FIT": "Garage and fitment (D-38)",
    "THM": "Frontend themes",
    "SEO": "SEO, feeds, analytics",
    "AI": "AI channels (MCP, agents)",
    "CMP": "Compliance",
    "SEC": "Security",
    "OPS": "Operations",
    "MIG": "Migration and cutover",
}

PHASES = {
    "F0": "Foundations",
    "F1": "Catalog, import, base frontend",
    "F2": "Checkout: tax, shipping, orders, payments",
    "F3": "Vendors, ledger, compliance",
    "F4": "Content, SEO, feeds",
    "F5": "Migration, acceptance testing, go-live",
    "F6": "After go-live (P2)",
    "F7": "Later evolution (P3)",
}

PHASE_BY_PREFIX = {
    "ARC": "F0",
    "CORE": "F0",
    "SEC": "F0",
    "OPS": "F0",
    "CAT": "F1",
    "ING": "F1",
    "I18N": "F1",
    "TAX": "F2",
    "SHP": "F2",
    "ORD": "F2",
    "PAY": "F2",
    "VEN": "F3",
    "CMP": "F3",
    "CMS": "F4",
    "EVT": "F4",
    "FIT": "F6",
    "THM": "F1",
    "SEO": "F4",
    "AI": "F4",
    "MIG": "F5",
}
PHASE_OVERRIDE = {
    "FIT.1": "F1",
    "FIT.2": "F1",
    "FIT.3": "F1",
    "FIT.4": "F1",
    "ARC.10": "F2",
    "SEC.6": "F5",
    "CMS.1": "F1",
    "THM.7": "F4",
    "THM.8": "F4",
    "THM.9": "F4",
    "THM.10": "F4",
}

ROW = re.compile(
    r"^\|\s*([A-Z0-9]+\.\d+)\s*\|\s*(.+?)\s*\|\s*(P[123])\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$"
)


@dataclass
class Task:
    id: str
    title: str
    priority: str
    hours: int
    deps: list[str]
    done: str
    source: str

    @property
    def prefix(self) -> str:
        return self.id.split(".")[0]

    @property
    def phase(self) -> str:
        if self.id in PHASE_OVERRIDE:
            return PHASE_OVERRIDE[self.id]
        if self.priority == "P2":
            return "F6"
        if self.priority == "P3":
            return "F7"
        return PHASE_BY_PREFIX[self.prefix]


def doc_files() -> list[Path]:
    return sorted(p for p in SPEC.glob("[0-9][0-9]-*.md") if p.name not in GENERATED)


def parse_tasks() -> list[Task]:
    tasks: list[Task] = []
    for path in doc_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = ROW.match(line)
            if not m:
                continue
            tid, title, pri, hours, deps, done = m.groups()
            dep_list = [] if deps.strip() in {"—", "-"} else [d.strip() for d in deps.split(",")]
            tasks.append(Task(tid, title, pri, int(hours), dep_list, done, path.name))
    return tasks


def validate(tasks: list[Task]) -> list[str]:
    errors: list[str] = []
    ids = [t.id for t in tasks]
    seen: set[str] = set()
    for tid in ids:
        if tid in seen:
            errors.append(f"duplicate ID: {tid}")
        seen.add(tid)
    by_id = {t.id: t for t in tasks}
    for t in tasks:
        if t.prefix not in MACRO:
            errors.append(f"{t.id}: unknown prefix")
        for d in t.deps:
            if d not in by_id:
                errors.append(f"{t.id}: missing dependency {d}")
    # cycle detection (DFS)
    state: dict[str, int] = {}

    def visit(tid: str, stack: list[str]) -> None:
        state[tid] = 1
        for d in by_id[tid].deps:
            if d not in by_id:
                continue
            if state.get(d) == 1:
                errors.append("cycle: " + " → ".join(stack + [tid, d]))
            elif state.get(d) is None:
                visit(d, stack + [tid])
        state[tid] = 2

    for tid in by_id:
        if state.get(tid) is None:
            visit(tid, [])
    return errors


def critical_path(tasks: list[Task], only_p1: bool = True) -> tuple[int, list[str]]:
    """Longest path (in hours) through the dependency graph: the lower bound of the schedule with
    unlimited parallelism."""
    by_id = {t.id: t for t in tasks if not only_p1 or t.priority == "P1"}
    memo: dict[str, tuple[int, list[str]]] = {}

    def longest(tid: str) -> tuple[int, list[str]]:
        if tid in memo:
            return memo[tid]
        t = by_id[tid]
        best = (0, [])
        for d in t.deps:
            if d in by_id:
                cand = longest(d)
                if cand[0] > best[0]:
                    best = cand
        memo[tid] = (best[0] + t.hours, best[1] + [tid])
        return memo[tid]

    return max((longest(t) for t in by_id), key=lambda x: x[0])


def md_backlog(tasks: list[Task]) -> str:
    total = sum(t.hours for t in tasks)
    by_pri = defaultdict(int)
    by_macro: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_phase: dict[str, list[Task]] = defaultdict(list)
    for t in tasks:
        by_pri[t.priority] += t.hours
        by_macro[t.prefix][t.priority] += t.hours
        by_macro[t.prefix]["n"] += 1
        by_phase[t.phase].append(t)
    cp_hours, cp = critical_path(tasks)

    out = [
        "# 90 — Backlog (generated)",
        "",
        "> Generated by `tools/build_docs.py` from the task tables in the domain documents.",
        "> **Do not edit by hand**: edit the domain document and regenerate (`make docs`).",
        "> The CSV for Jira/Linear/GitHub Projects is in [backlog.csv](backlog.csv).",
        "",
        "## Summary",
        "",
        f"- Micro-tasks: **{len(tasks)}** in **{len(by_macro)}** macro areas",
        f"- Estimated hours (development + automated tests, AI-assisted development): **{total} h** "
        f"(P1 {by_pri['P1']} h · P2 {by_pri['P2']} h · P3 {by_pri['P3']} h)",
        f"- P1 critical path (schedule lower bound with full parallelism): **{cp_hours} h** — "
        + " → ".join(cp),
        "",
        "## By macro area",
        "",
        "| Macro | Area | Tasks | P1 h | P2 h | P3 h | Total h |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for prefix in MACRO:
        m = by_macro.get(prefix)
        if not m:
            continue
        tot = m["P1"] + m["P2"] + m["P3"]
        out.append(
            f"| {prefix} | {MACRO[prefix]} | {m['n']} | {m['P1']} | {m['P2']} | {m['P3']} | {tot} |"
        )
    out.append(
        f"| **Total** | | **{len(tasks)}** | **{by_pri['P1']}** | **{by_pri['P2']}** | "
        f"**{by_pri['P3']}** | **{total}** |"
    )
    out += ["", "## By phase", ""]
    for ph, name in PHASES.items():
        items = by_phase.get(ph, [])
        if not items:
            continue
        hrs = sum(t.hours for t in items)
        out += [
            f"### {ph} — {name} ({len(items)} tasks, {hrs} h)",
            "",
            "| ID | Micro-task | Pri | Hours | Depends on | Done when |",
            "|---|---|---|---:|---|---|",
        ]
        for t in sorted(
            items, key=lambda x: (list(MACRO).index(x.prefix), int(x.id.split(".")[1]))
        ):
            deps = ", ".join(t.deps) or "—"
            out.append(f"| {t.id} | {t.title} | {t.priority} | {t.hours} | {deps} | {t.done} |")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def csv_text(tasks: list[Task]) -> str:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(
        [
            "id",
            "macro",
            "area",
            "phase",
            "title",
            "priority",
            "hours",
            "depends_on",
            "done_when",
            "document",
        ]
    )
    for t in tasks:
        w.writerow(
            [
                t.id,
                t.prefix,
                MACRO[t.prefix],
                f"{t.phase} {PHASES[t.phase]}",
                t.title,
                t.priority,
                t.hours,
                " ".join(t.deps),
                t.done,
                t.source,
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------- HTML

CSS = """
:root{--fg:#1d2330;--muted:#5b6475;--bg:#fff;--side:#f5f6f8;--line:#dfe3ea;--acc:#b3261e;--code:#f3f4f6}
@media (prefers-color-scheme: dark){:root{--fg:#e7e9ee;--muted:#a3abba;--bg:#14171c;--side:#1b1f26;--line:#2c323c;--acc:#ff8a80;--code:#20252d}}
*{box-sizing:border-box}
body{margin:0;font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--fg);background:var(--bg)}
a{color:var(--acc)}
.layout{display:grid;grid-template-columns:300px 1fr;min-height:100vh}
nav{position:sticky;top:0;height:100vh;overflow:auto;background:var(--side);border-right:1px solid var(--line);padding:20px 16px;font-size:14px}
nav h1{font-size:16px;margin:0 0 4px}
nav p{color:var(--muted);margin:0 0 16px;font-size:13px}
nav ol{padding-left:18px;margin:0}
nav li{margin:4px 0}
nav input{width:100%;padding:6px 8px;margin:0 0 12px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
main{max-width:1100px;padding:24px 40px 80px}
section.doc{border-bottom:2px solid var(--line);padding-bottom:32px;margin-bottom:32px}
h1,h2,h3{line-height:1.25}
h1{font-size:28px;margin-top:8px}
h2{font-size:22px;margin-top:32px;border-bottom:1px solid var(--line);padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--side)}
code{background:var(--code);padding:1px 4px;border-radius:4px;font-size:.9em}
pre{background:var(--code);padding:12px 14px;border-radius:8px;overflow-x:auto;font-size:13px;line-height:1.45}
pre code{background:none;padding:0}
pre.mermaid{background:transparent;text-align:center}
blockquote{margin:12px 0;padding:8px 14px;border-left:4px solid var(--acc);background:var(--side)}
.badge{display:inline-block;font-size:12px;padding:1px 6px;border-radius:10px;background:var(--side);border:1px solid var(--line);color:var(--muted)}
@media (max-width:900px){.layout{grid-template-columns:1fr}nav{position:static;height:auto}main{padding:16px}}
@media print{nav{display:none}.layout{display:block}section.doc{page-break-after:always}}
"""

JS = """
document.getElementById('q').addEventListener('input',e=>{
  const q=e.target.value.toLowerCase();
  document.querySelectorAll('main tr').forEach(tr=>{tr.style.display=!q||tr.textContent.toLowerCase().includes(q)||tr.querySelector('th')?'':'none'});
});
"""


def doc_anchor(name: str) -> str:
    return "doc-" + name.removesuffix(".md")


def to_html(md_text: str, md) -> str:
    md.reset()
    body = md.convert(md_text)
    # mermaid code fences -> <pre class="mermaid">
    body = re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        lambda m: f'<pre class="mermaid">{m.group(1)}</pre>',
        body,
        flags=re.S,
    )
    # cross-document links -> in-page anchors
    body = re.sub(
        r'href="(\d\d-[a-z0-9-]+\.md)(#[^"]*)?"',
        lambda m: f'href="#{doc_anchor(m.group(1))}"',
        body,
    )
    body = body.replace('href="README.md"', 'href="#doc-README"').replace(
        'href="backlog.csv"', 'href="../docs/spec/backlog.csv"'
    )
    return body


def build_html(files: list[Path], out: Path) -> None:
    import markdown

    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc"])
    nav, sections = [], []
    for p in files:
        text = p.read_text(encoding="utf-8")
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
        anchor = doc_anchor(p.name)
        nav.append(f'<li><a href="#{anchor}">{html.escape(title)}</a></li>')
        sections.append(
            f'<section class="doc" id="{anchor}"><span class="badge">{p.name}</span>'
            f"{to_html(text, md)}</section>"
        )
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MPMV — Rebuild specification</title><style>{CSS}</style></head>
<body><div class="layout"><nav aria-label="Contents"><h1>MPMV</h1><p>Multivendor marketplace rebuild specification</p>
<input id="q" type="search" placeholder="Filter table rows…" aria-label="Filter table rows">
<ol>{"".join(nav)}</ol></nav><main>{"".join(sections)}</main></div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true,theme:window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'default'}});</script>
<script>{JS}</script></body></html>"""
    out.write_text(page.rstrip("\n") + "\n", encoding="utf-8")


def validate_progress(tasks: list[Task]) -> list[str]:
    """Every task ID listed in docs/progress.md must exist in the spec, once."""
    if not PROGRESS.exists():
        return []
    ids = {t.id for t in tasks}
    seen: set[str] = set()
    errors = []
    for line in PROGRESS.read_text(encoding="utf-8").splitlines():
        m = PROGRESS_ROW.match(line)
        if not m:
            continue
        tid = m.group(1)
        if tid not in ids:
            errors.append(f"docs/progress.md: unknown task {tid}")
        if tid in seen:
            errors.append(f"docs/progress.md: {tid} listed twice")
        seen.add(tid)
    return errors


def main() -> int:
    check = "--check" in sys.argv[1:]
    tasks = parse_tasks()
    errors = validate(tasks) + validate_progress(tasks)
    if errors:
        print("\n".join(errors))
        return 1

    generated = {
        SPEC / "90-backlog.md": md_backlog(tasks),
        SPEC / "backlog.csv": csv_text(tasks),
    }
    if check:
        stale = [
            p
            for p, text in generated.items()
            if not p.exists() or p.read_text(encoding="utf-8") != text
        ]
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)} (run: uv run tools/build_docs.py)")
        return 1 if stale else 0

    for p, text in generated.items():
        p.write_text(text, encoding="utf-8")
    BUILD.mkdir(exist_ok=True)
    build_html([SPEC / "README.md"] + sorted(SPEC.glob("[0-9][0-9]-*.md")), HTML_OUT)

    total = sum(t.hours for t in tasks)
    p1 = sum(t.hours for t in tasks if t.priority == "P1")
    cp_hours, _ = critical_path(tasks)
    print(f"OK: {len(tasks)} tasks, {total} h (P1 {p1} h), P1 critical path {cp_hours} h")
    print(f"HTML: {HTML_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
