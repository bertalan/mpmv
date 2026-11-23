"""THM.29: stable anchors of the legal sections."""

from apps.cms.engine import legal_section_anchors

A, B, C = "aaaaaaaa-1111", "bbbbbbbb-2222", "cccccccc-3333"


def test_unique_tags_get_the_plain_anchor() -> None:
    assert legal_section_anchors([("redress", A), ("termination", B)]) == [
        "section-redress",
        "section-termination",
    ]


def test_a_duplicate_gets_its_block_id_prefix() -> None:
    assert legal_section_anchors([("redress", A), ("redress", B)]) == [
        "section-redress",
        "section-redress-bbbbbbbb",
    ]


def test_anchors_stay_with_their_blocks_when_sections_are_reordered() -> None:
    before = dict(
        zip([A, B, C], legal_section_anchors([("x", A), ("y", B), ("x", C)]), strict=True)
    )
    after = dict(zip([C, B, A], legal_section_anchors([("x", C), ("y", B), ("x", A)]), strict=True))
    assert before[B] == after[B] == "section-y"
    # Only the pair that shares a tag swaps which one holds the plain anchor.
    assert before[A] == "section-x"
    assert after[A] == "section-x-aaaaaaaa"
    assert before[C] == "section-x-cccccccc"
    assert after[C] == "section-x"


def test_empty_body() -> None:
    assert legal_section_anchors([]) == []
