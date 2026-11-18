"""ING.4: the pure extraction rules, checked against 50 generic sample products whose expected
values were produced by the legacy step-5 functions (pseudonymised vocabulary)."""

import json
from pathlib import Path
from typing import Any

import pytest

from apps.ingest import extraction
from apps.ingest.engine import MappingEntry

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "extraction_samples.json").read_text()
)


def entry(
    kind: str, key: str, ref: int, *, aliases: tuple[str, ...] = (), value: str = ""
) -> MappingEntry:
    return MappingEntry(
        vendor_id=1, kind=kind, key=key, aliases=aliases, target_type="", target_id=None,
        target_value=value or key, ref=ref,
    )  # fmt: skip


def fixture_rules() -> tuple[extraction.ExtractionRules, dict[str, MappingEntry]]:
    entries: list[MappingEntry] = []
    for word, code in FIXTURE["types"].items():
        entries.append(entry("product_type", word, len(entries) + 1, value=code))
    for name, info in FIXTURE["materials"].items():
        entries.append(
            entry(
                "material",
                name,
                len(entries) + 1,
                aliases=tuple(info["variants"]),
                value=info["it"],
            )
        )
    for word, english in FIXTURE["colors"].items():
        entries.append(entry("color", word, len(entries) + 1, value=english))
    return extraction.build_rules(entries), {str(e.ref): e for e in entries}


def translate(found: dict[str, Any], by_ref: dict[str, MappingEntry]) -> dict[str, Any]:
    out = dict(found)
    if "product_type" in out:
        out["product_type"] = by_ref[out["product_type"]].target_value
    if "materials" in out:
        out["materials"] = [by_ref[r].target_value for r in out["materials"]]
    if "colors" in out:
        out["colors"] = list(dict.fromkeys(by_ref[r].target_value for r in out["colors"]))
    return out


def test_the_fixture_holds_fifty_samples() -> None:
    assert len(FIXTURE["samples"]) == 50


@pytest.mark.parametrize("sample", FIXTURE["samples"], ids=lambda s: s["title"])
def test_extraction_matches_the_legacy_step_5_output(sample: dict[str, Any]) -> None:
    rules, by_ref = fixture_rules()
    found = translate(
        extraction.extract_fields(sample["title"], sample["description"], rules), by_ref
    )
    assert found == sample["expected"]


# --- Helpers


def test_first_match_follows_candidate_order_not_text_order() -> None:
    cands = (extraction.Candidate("a", ("side bag",)), extraction.Candidate("b", ("bag",)))
    assert extraction.first_match("A BAG and a side bag", cands) == "a"
    assert extraction.first_match("", cands) is None


def test_word_matches_needs_whole_words_and_keeps_two_by_default() -> None:
    cands = tuple(extraction.Candidate(w, (w,)) for w in ("red", "blue", "green"))
    assert extraction.word_matches(["tired of blue, green and red"], cands, limit=2) == [
        "blue",
        "green",
    ]
    assert extraction.word_matches(["tired"], cands, limit=2) == []
    assert extraction.word_matches([None, "red red"], cands, limit=2) == ["red"]


def test_model_stops_at_a_common_word_once_one_word_is_kept() -> None:
    assert extraction.extract_model("Bag modello Alpha per bikes", frozenset({"per"})) == "ALPHA"
    assert extraction.extract_model("Plain title", frozenset()) is None


# --- CAT.22 fields


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Size 30 x 20 x 10 cm", {"length_mm": 300, "width_mm": 200, "height_mm": 100}),
        ("Size 30,5\u00d720 cm", {"length_mm": 305, "width_mm": 200}),
        ("0,45 x 0,3 x 0,2 m", {"length_mm": 450, "width_mm": 300, "height_mm": 200}),
        ("300 x 200 x 100 mm", {"length_mm": 300, "width_mm": 200, "height_mm": 100}),
        ("30 x 20 x 10", {}),  # no unit: never a guess
        ("0 x 20 x 10 cm", {}),  # a dimension of 0 is refused (CAT.22)
        (None, {}),
    ],
)
def test_dimensions_become_millimetres(text: str | None, expected: dict[str, int]) -> None:
    assert extraction.extract_dimensions_mm(text) == expected


COUNTRIES = {"italy": "IT", "italia": "IT", "united kingdom": "GB"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Country of origin: IT", "IT"),
        ("Made in Italy.", "IT"),
        ("Paese di origine: Italia", "IT"),
        ("made in United Kingdom, boxed", "GB"),
        ("Made in Atlantis", None),  # unknown name: no guess
        ("country of origin: it", None),  # a lowercase code is not an ISO code
        ("nothing here", None),
    ],
)
def test_country_of_origin_is_an_iso_code_or_nothing(text: str, expected: str | None) -> None:
    assert extraction.extract_country_of_origin(text, COUNTRIES) == expected


def test_extract_fields_carries_the_cat22_fields() -> None:
    rules = extraction.ExtractionRules(countries=COUNTRIES)
    got = extraction.extract_fields("Case", "Dimensions 30 x 20 x 10 cm. Made in Italy", rules)
    assert got == {"length_mm": 300, "width_mm": 200, "height_mm": 100, "country_of_origin": "IT"}
