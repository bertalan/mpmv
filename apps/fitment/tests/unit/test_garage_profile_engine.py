"""FIT.5 (1/3): the pure rules of the account garage profiles (no database)."""

from apps.fitment import engine


def test_a_profile_name_is_one_to_forty_characters_after_cleaning() -> None:
    assert engine.profile_name_error("My car") is None
    assert engine.profile_name_error("   ") is not None
    assert engine.profile_name_error("x" * 40) is None
    assert engine.profile_name_error("x" * 41) is not None


def test_the_default_name_is_the_node_labels_and_the_year() -> None:
    assert engine.default_profile_name(["Make One", "Model A"], 2019) == "Make One Model A 2019"
    assert engine.default_profile_name(["Make One"], None) == "Make One"
    long = engine.default_profile_name(["M" * 30, "N" * 30], 2019)
    assert len(long) <= engine.MAX_PROFILE_NAME


def test_a_copy_name_is_free_among_the_taken_names() -> None:
    assert engine.copy_name("Car", ["Car"]) == "Car (copy)"
    assert engine.copy_name("Car", ["Car", "Car (copy)"]) == "Car (copy 2)"
    assert engine.copy_name("Car", ["Car", "car (COPY)", "Car (copy 2)"]) == "Car (copy 3)"
    assert len(engine.copy_name("x" * 40, ["x" * 40])) <= engine.MAX_PROFILE_NAME


def test_the_profile_limit_refuses_the_one_past_the_maximum() -> None:
    assert engine.profile_limit_error(9, 10) is None
    assert engine.profile_limit_error(10, 10) is not None


def test_a_year_needs_a_scheme_with_years_and_must_be_in_range() -> None:
    assert engine.profile_year_error(None, enabled=False, low=1900, high=2030) is None
    assert engine.profile_year_error(2019, enabled=False, low=1900, high=2030) is not None
    assert engine.profile_year_error(2019, enabled=True, low=1900, high=2030) is None
    assert engine.profile_year_error(1899, enabled=True, low=1900, high=2030) is not None
    assert engine.profile_year_error(2031, enabled=True, low=1900, high=2030) is not None


def test_a_retired_node_marks_its_profiles() -> None:
    assert engine.profile_node_retired(engine.RETIRED) is True
    assert engine.profile_node_retired(engine.ACTIVE) is False
