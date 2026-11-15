"""ARC.34: prove that the CI test shards cover the whole collection, each test exactly once.

Collects the "unit or integration" suites once without a shard and once per shard
(`MPMV_TEST_SHARD=i/N`, see conftest.py), then fails when the shards overlap or their union
differs from the full collection. Collection needs no database.

Usage: uv run python tools/check_test_shards.py [SHARDS]   (default 4)
"""

import os
import subprocess
import sys


def collect(shard: str | None) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k != "MPMV_TEST_SHARD"}
    if shard is not None:
        env["MPMV_TEST_SHARD"] = shard
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "unit or integration"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
        raise SystemExit(f"collection failed (shard {shard or 'none'})")
    return [line for line in result.stdout.splitlines() if "::" in line]


def main() -> int:
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    full = collect(None)
    seen: dict[str, int] = {}
    sizes: list[int] = []
    for index in range(1, total + 1):
        ids = collect(f"{index}/{total}")
        sizes.append(len(ids))
        for test_id in ids:
            seen[test_id] = seen.get(test_id, 0) + 1
    duplicated = sorted(t for t, n in seen.items() if n > 1)
    missing = sorted(set(full) - set(seen))
    extra = sorted(set(seen) - set(full))
    print(f"full collection: {len(full)} tests; shard sizes: {sizes}")
    for label, ids in (
        ("run twice", duplicated),
        ("in no shard", missing),
        ("not in the full run", extra),
    ):
        if ids:
            print(f"{len(ids)} tests {label}, first: {ids[:5]}")
    if duplicated or missing or extra or sum(sizes) != len(full):
        return 1
    print("OK: every test runs in exactly one shard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
