"""Every bold label in the help files is a screen label of the shipped catalog (ARC.37)."""

from tests.help_labels import unknown_labels


def test_bold_labels_match_the_shipped_catalog_everywhere() -> None:
    assert unknown_labels() == []
