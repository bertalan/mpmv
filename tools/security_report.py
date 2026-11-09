#!/usr/bin/env python3
"""Report a scheduled security run on GitHub issues (SEC.5).

A failing run opens one issue labelled `security` per workflow (title prefix `[<workflow>]`), or
updates the open one, and posts one line on the lane board (#86) when the issue is opened. A green
run closes the open issue with a comment. The text holds counts, job results and test names only:
never a URL other than the run link, a host, an address or a secret, because the repository is
public.

Standard library only: it runs on the bare CI runner with `gh` and `GH_TOKEN` from the workflow.

    python3 tools/security_report.py --workflow security-nightly --run-url URL \
        --result tests=failure --result audit=success --junit build/reports/junit-security.xml \
        --count osv_vulns=2
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

LABEL = "security"
LANE_BOARD = "86"
MAX_NAMES = 20
MAX_NAME_LENGTH = 160
PYTHON_ROOTS = ("tests.", "apps.", "mpmv.")
# Anything that could name a server: URLs, IPv4/IPv6 literals, dotted host names.
_URL = re.compile(r"[a-zA-Z][\w+.-]*://\S*")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6 = re.compile(r"[0-9a-fA-F:.]*:[0-9a-fA-F:.]*:[0-9a-fA-F:.]*")
_HOST = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")
_COUNT = re.compile(r"^[a-z][a-z0-9_]*=\d+$")
_RESULT = re.compile(r"^[a-z][a-z0-9_-]*=(success|failure|cancelled|skipped)$")


@dataclass
class Summary:
    total: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    names: list[str] = field(default_factory=list)


def _ipv6(match: re.Match[str]) -> str:
    try:
        ipaddress.IPv6Address(match.group(0))
    except ValueError:
        return match.group(0)
    return "<addr>"


def _host(match: re.Match[str]) -> str:
    # Dotted Python paths (tests.security.unit) look like host names; keep them.
    return match.group(0) if match.group(0).startswith(PYTHON_ROOTS) else "<host>"


def redact(text: str) -> str:
    """Drop URLs, addresses and host names: a parametrised test id may carry one."""
    text = _URL.sub("<url>", text)
    text = _IPV4.sub("<addr>", text)
    text = _IPV6.sub(_ipv6, text)
    return _HOST.sub(_host, text)


def case_name(classname: str, name: str) -> str:
    """`module::test`, redacted part by part (the `::` separator itself parses as IPv6)."""
    return f"{redact(classname)}::{redact(name)}"[:MAX_NAME_LENGTH]


def junit_summary(paths: list[Path]) -> Summary:
    summary = Summary()
    for path in paths:
        if not path.is_file():
            continue
        for case in ET.parse(path).getroot().iter("testcase"):
            summary.total += 1
            name = case_name(case.get("classname", ""), case.get("name", ""))
            if case.find("failure") is not None:
                summary.failed += 1
                summary.names.append(name)
            elif case.find("error") is not None:
                summary.errors += 1
                summary.names.append(name)
            elif case.find("skipped") is not None:
                summary.skipped += 1
    return summary


def parse_counts(values: list[str]) -> dict[str, str]:
    counts: dict[str, str] = {}
    for value in values:
        if not _COUNT.match(value):
            raise ValueError(f"a count must be name=<number>: {value!r}")
        key, number = value.split("=", 1)
        counts[key] = number
    return counts


def parse_results(values: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    for value in values:
        if not _RESULT.match(value):
            raise ValueError(f"a result must be job=<success|failure|cancelled|skipped>: {value!r}")
        job, result = value.split("=", 1)
        results[job] = result
    return results


def title_prefix(workflow: str) -> str:
    return f"[{workflow}]"


def issue_title(workflow: str) -> str:
    return f"{title_prefix(workflow)} scheduled security run failing"


def board_line(workflow: str, run_url: str) -> str:
    return f"to lane-a: {workflow} failed: {run_url}"


def pick_issue(issues: list[dict[str, object]], workflow: str) -> int | None:
    for issue in issues:
        if str(issue.get("title", "")).startswith(title_prefix(workflow)):
            number = issue.get("number")
            if isinstance(number, int):
                return number
    return None


def issue_body(
    *,
    workflow: str,
    run_url: str,
    results: dict[str, str],
    counts: dict[str, str],
    summary: Summary,
) -> str:
    lines = [
        f"The scheduled `{workflow}` run failed: {run_url}",
        "",
        "Jobs:",
        *(f"- {job}: {result}" for job, result in sorted(results.items())),
    ]
    if counts:
        lines += ["", "Findings:", *(f"- {key}: {value}" for key, value in sorted(counts.items()))]
    if summary.total:
        lines += [
            "",
            f"Tests: {summary.total} run, {summary.failed} failed, {summary.errors} errors, "
            f"{summary.skipped} skipped.",
        ]
    if summary.names:
        lines += ["", "Failing tests:", *(f"- `{name}`" for name in summary.names[:MAX_NAMES])]
        if len(summary.names) > MAX_NAMES:
            lines.append(f"- and {len(summary.names) - MAX_NAMES} more")
    lines += [
        "",
        "Details are in the run log. This issue closes itself on the next green run.",
        "Server details stay out of this public issue.",
    ]
    return "\n".join(lines)


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def open_issues() -> list[dict[str, object]]:
    found = json.loads(
        gh(
            "issue",
            "list",
            "--label",
            LABEL,
            "--state",
            "open",
            "--json",
            "number,title",
            "--limit",
            "100",
        )
    )
    assert isinstance(found, list)
    return found


def report(workflow: str, run_url: str, failed: bool, body: str) -> None:
    number = pick_issue(open_issues(), workflow)
    if not failed:
        if number is not None:
            gh("issue", "comment", str(number), "--body", f"Green run: {run_url}")
            gh("issue", "close", str(number), "--reason", "completed")
        return
    if number is not None:
        gh("issue", "edit", str(number), "--body", body)
        gh("issue", "comment", str(number), "--body", f"Still failing: {run_url}")
        return
    gh(
        "label",
        "create",
        LABEL,
        "--color",
        "b60205",
        "--force",
        "--description",
        "Scheduled security runs",
    )
    gh("issue", "create", "--title", issue_title(workflow), "--label", LABEL, "--body", body)
    gh("issue", "comment", LANE_BOARD, "--body", board_line(workflow, run_url))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True, choices=["security-nightly", "security-stage"])
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--result", action="append", default=[], help="job=result")
    parser.add_argument("--count", action="append", default=[], help="name=number")
    parser.add_argument("--junit", action="append", default=[], type=Path)
    args = parser.parse_args(argv)

    results = parse_results(args.result)
    ran = {job: result for job, result in results.items() if result != "skipped"}
    if not ran:
        print("No job ran: nothing to report.")
        return
    failed = any(result != "success" for result in ran.values())
    body = issue_body(
        workflow=args.workflow,
        run_url=args.run_url,
        results=results,
        counts=parse_counts(args.count),
        summary=junit_summary(args.junit),
    )
    report(args.workflow, args.run_url, failed, body)
    print("failure reported" if failed else "green run")


if __name__ == "__main__":
    main()
