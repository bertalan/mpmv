"""TAX.11: stored territory postcode ranges move from `a-b` to the shared `a..b` syntax.

Only two numbers of the same length joined by a dash were ranges; PT and PL postcodes with a
dash stay exact postcodes. The migration is reversible, except for patterns written later that the
old grammar cannot hold: then the reverse step refuses to run.
"""

import pytest
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor

from apps.tax.models import Territory

BEFORE = [("tax", "0002_regime_change_request")]
AFTER = [("tax", "0003_territory_postcode_ranges")]

OLD_PATTERNS = ["22100-22199", "35*", "9000-001", "00-950", "23041", "BT*", "1-22", "22299-22200"]
NEW_PATTERNS = ["22100..22199", "35*", "9000-001", "00-950", "23041", "BT*", "1-22", "22200..22299"]
# A reversed old range comes back with its bounds in order: same postcodes, valid in both grammars.
REVERSED_OLD_PATTERNS = [*OLD_PATTERNS[:-1], "22200-22299"]


def _territory_patterns(executor: MigrationExecutor, state: list[tuple[str, str]]) -> list[str]:
    model = executor.loader.project_state(state).apps.get_model("tax", "Territory")
    return list(model.objects.get(code="IT-T1").postcode_patterns)


@pytest.mark.django_db(transaction=True)
def test_stored_ranges_move_to_the_shared_syntax_and_back() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old_apps = executor.loader.project_state(BEFORE).apps
    OldTerritory = old_apps.get_model("tax", "Territory")
    OldTerritory.objects.create(
        code="IT-T1",
        name="Territory 1",
        country="IT",
        vat_status="own_rates",
        postcode_patterns=OLD_PATTERNS,
    )
    OldTerritory.objects.create(
        code="IT-T2", name="Territory 2", country="IT", vat_status="own_rates"
    )

    executor.loader.build_graph()
    executor.migrate(AFTER)
    assert _territory_patterns(executor, AFTER) == NEW_PATTERNS

    executor.loader.build_graph()
    executor.migrate(BEFORE)
    assert _territory_patterns(executor, BEFORE) == REVERSED_OLD_PATTERNS

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    territory = Territory.objects.get(code="IT-T1")
    assert territory.postcode_patterns == NEW_PATTERNS
    territory.full_clean()  # every migrated pattern is valid in the shared grammar
    assert territory.matches("IT", "22150") is True
    assert territory.matches("IT", "22250") is True
    assert Territory.objects.get(code="IT-T2").postcode_patterns == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("pattern", ["BT1..BT9", "1000-001..1999-999", "AB-1*"])
def test_the_reverse_step_refuses_patterns_the_old_grammar_cannot_hold(pattern: str) -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
    Territory.objects.create(
        code="GB-T1",
        name="Territory 1",
        country="GB",
        vat_status="own_rates",
        postcode_patterns=[pattern],
    )
    executor.loader.build_graph()
    with pytest.raises(IrreversibleError, match="GB-T1"):
        executor.migrate(BEFORE)

    # Nothing was rewritten and the schema stayed on the shared grammar.
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    assert Territory.objects.get(code="GB-T1").postcode_patterns == [pattern]
