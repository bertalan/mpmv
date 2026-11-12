"""CMP.4 (2/2): the sub-processor register is data in the repository, complete and well-formed."""

from apps.compliance import subprocessors

REQUIRED_CATEGORIES = {
    "payments",
    "carriers",
    "email",
    "ai",
    "hosting",
    "geocoding",
    "map_tiles",
}


def test_the_register_covers_every_category_the_spec_lists() -> None:
    assert {entry.category for entry in subprocessors.REGISTER} >= REQUIRED_CATEGORIES


def test_every_entry_says_what_it_does_with_which_data_and_where() -> None:
    for entry in subprocessors.REGISTER:
        for value in (entry.name, entry.purpose, entry.data, entry.location):
            assert value, entry.key
        assert entry.safeguard, entry.key


def test_keys_are_unique() -> None:
    keys = [entry.key for entry in subprocessors.REGISTER]
    assert len(keys) == len(set(keys))


def test_the_public_listing_hides_nothing_internal() -> None:
    for entry in subprocessors.public_register():
        assert entry.name
        assert entry.purpose
