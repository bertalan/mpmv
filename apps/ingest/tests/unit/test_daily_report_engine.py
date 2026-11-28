"""ING.10: the pure daily report builder."""

import datetime as dt

from apps.ingest import engine
from apps.ingest.engine import ReportCollision, ReportReview, ReportRun

DAY = dt.date(2026, 11, 28)


def run(source: str = "s1", **kwargs: object) -> ReportRun:
    base: dict[str, object] = {
        "source": source, "vendor": "Vendor A", "kind": "stock", "status": "succeeded",
        "dry_run": False, "seen": 10, "updated": 3, "unchanged": 6, "skipped": 1, "errors": 0,
        "error_text": "",
    }  # fmt: skip
    return ReportRun(**{**base, **kwargs})  # type: ignore[arg-type]


def review(kind: str = "price_jump", **kwargs: object) -> ReportReview:
    base: dict[str, object] = {
        "source": "s1", "vendor": "Vendor A", "kind": kind, "sku": "AB-1", "open": True,
        "opened_today": True, "blocking": False,
    }  # fmt: skip
    return ReportReview(**{**base, **kwargs})  # type: ignore[arg-type]


def build(**kwargs: object) -> engine.DailyReport:
    parts: dict[str, object] = {
        "runs": [], "reviews": [], "collisions": [], "missing_items": 0, "unavailable": [],
    }  # fmt: skip
    return engine.build_daily_report(DAY, **{**parts, **kwargs})  # type: ignore[arg-type]


def test_an_empty_day_is_a_quiet_report() -> None:
    report = build()
    assert report.day == DAY
    assert report.runs_total == 0
    assert report.anomalies == []
    assert report.needs_attention is False


def test_run_counters_are_summed_and_failures_counted() -> None:
    report = build(
        runs=[run(), run("s2", status="failed", errors=1, error_text="RuntimeError: boom", seen=0)]
    )
    assert (report.runs_total, report.runs_failed) == (2, 1)
    assert report.totals["seen"] == 10
    assert report.totals["errors"] == 1
    assert [line.source for line in report.error_runs] == ["s2"]
    assert report.needs_attention is True


def test_error_text_is_capped() -> None:
    report = build(runs=[run(status="failed", errors=1, error_text="x" * 900)])
    assert len(report.error_runs[0].error_text) <= engine.REPORT_ERROR_CHARS


def test_reviews_opened_today_are_counted_by_kind_in_a_fixed_order() -> None:
    report = build(
        reviews=[review("stale"), review("price_jump"), review("price_jump"),
                 review("sale", opened_today=False)]
    )  # fmt: skip
    assert report.opened_by_kind == {"price_jump": 2, "sale": 0, "identity_change": 0, "stale": 1,
                                     "duplicate_sku": 0}  # fmt: skip
    assert list(report.opened_by_kind) == list(engine.REVIEW_KINDS)


def test_open_and_blocking_totals_and_stale_items() -> None:
    report = build(
        reviews=[review("stale"), review("duplicate_sku", blocking=True),
                 review("price_jump", open=False)],
        missing_items=4,
    )  # fmt: skip
    assert (report.open_total, report.open_blocking) == (2, 1)
    assert (report.stale_open, report.missing_items) == (1, 4)


def test_identity_anomalies_come_from_reviews_and_collisions() -> None:
    report = build(
        reviews=[
            review("identity_change", sku="B-2"),
            review("duplicate_sku", sku="A-1"),
            review("price_jump"),
            review("identity_change", sku="OLD", open=False, opened_today=False),
        ],
        collisions=[ReportCollision(vendor="Vendor A", sku_key="ab1", items=2)],
    )
    kinds = [(a.kind, a.sku) for a in report.anomalies]
    assert kinds == [
        ("duplicate_sku", "A-1"),
        ("identity_change", "B-2"),
        ("sku_collision", "ab1"),
    ]
    assert report.needs_attention is True


def test_the_same_sku_of_two_vendors_stays_two_separate_lines() -> None:
    report = build(
        reviews=[
            review("duplicate_sku", vendor="Vendor A", source="a", sku="X-1"),
            review("duplicate_sku", vendor="Vendor B", source="b", sku="X-1"),
        ]
    )
    assert [(a.vendor, a.sku) for a in report.anomalies] == [
        ("Vendor A", "X-1"),
        ("Vendor B", "X-1"),
    ]


def test_unavailable_sections_are_listed_and_need_attention() -> None:
    report = build(unavailable=["s9"])
    assert report.unavailable == ["s9"]
    assert report.needs_attention is True


def test_the_report_is_json_ready() -> None:
    import json

    report = build(runs=[run()], reviews=[review("identity_change")])
    json.dumps(engine.report_context(report))
    assert engine.report_context(report)["day"] == "2026-11-28"
