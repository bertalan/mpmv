"""CMP.8: the pure origin rules (no database)."""

from apps.catalog import ai_labels


def test_only_ai_images_have_a_source_type() -> None:
    assert ai_labels.iptc_source_type(ai_labels.AI_NONE) == ""
    assert ai_labels.iptc_source_type(ai_labels.AI_GENERATED).endswith("/trainedAlgorithmicMedia")


def test_machine_text_leaves_draft_only_after_a_person_reviewed_it() -> None:
    assert not ai_labels.may_leave_draft("machine", reviewed_by_person=False)
    assert ai_labels.may_leave_draft("machine", reviewed_by_person=True)
    assert ai_labels.may_leave_draft("human", reviewed_by_person=False)
