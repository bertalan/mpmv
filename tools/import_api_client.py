#!/usr/bin/env python3
"""Sample client of the vendor import API (ING.12): upsert, stock and prices, dry-run first.

    MPMV_API_KEY=... python tools/import_api_client.py https://example.org feed.json
    MPMV_API_KEY=... python tools/import_api_client.py https://example.org feed.json --apply

`feed.json` holds `{"products": [...], "stock": [...], "prices": [...]}` with the row shapes of
`/api/v1/import/*` (see /api/v1/docs). By default every call is a dry run: it prints what would
change and writes nothing. `--apply` repeats the calls for real, and only when the dry run
reported no refused row. Standard library only; the key comes from the environment, never argv.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

# (method, path, JSON body) -> (status, JSON answer)
Transport = Callable[[str, str, dict[str, Any]], tuple[int, dict[str, Any]]]

STEPS = (
    ("products", "POST", "/api/v1/import/products"),
    ("stock", "PUT", "/api/v1/import/stock"),
    ("prices", "PUT", "/api/v1/import/prices"),
)


def http_transport(base_url: str, api_key: str) -> Transport:
    def send(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(  # noqa: S310 (the operator names the https base URL)
            base_url.rstrip("/") + path,
            data=json.dumps(body).encode(),
            method=method,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:  # RFC 9457 problem details
            return error.code, json.loads(error.read() or b"{}")

    return send


def run(feed: dict[str, Any], send: Transport, *, apply: bool = False) -> list[str]:
    """Send each part of `feed` as a dry run; with `apply`, again for real if all were clean.

    Returns report lines. Stops at the first problem (a non-200 answer) or refused upsert row.
    """
    report: list[str] = []
    for write in [False, True] if apply else [False]:
        mode = "apply" if write else "dry-run"
        for name, method, path in STEPS:
            if not feed.get(name):
                continue
            query = "" if write else "?dry_run=true"
            status, answer = send(method, path + query, {"items": feed[name]})
            if status != 200:
                report.append(f"{mode} {name}: refused {status} {answer.get('detail', '')}")
                return report
            refused = [row for row in answer.get("rows", []) if row["outcome"] == "refused"]
            summary = answer["summary"]
            report.append(f"{mode} {name}: {summary}, refused rows: {len(refused)}")
            if refused:
                report.extend(f"  row {r['index']} {r['sku']}: refused" for r in refused)
                return report
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])  # type: ignore[union-attr]
    parser.add_argument("base_url")
    parser.add_argument("feed", help="JSON file with products, stock and prices")
    parser.add_argument("--apply", action="store_true", help="write for real after a clean dry run")
    args = parser.parse_args(argv)
    api_key = os.environ.get("MPMV_API_KEY", "")
    if not api_key:
        print("Set MPMV_API_KEY.", file=sys.stderr)
        return 2
    with open(args.feed, encoding="utf-8") as handle:
        feed = json.load(handle)
    lines = run(feed, http_transport(args.base_url, api_key), apply=args.apply)
    print("\n".join(lines))
    return 1 if any("refused" in line for line in lines) else 0


if __name__ == "__main__":
    sys.exit(main())
