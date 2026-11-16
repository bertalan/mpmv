"""Failure messages of the control tests (owner, 2026-11-16: "control tests with intelligible
answers"): every classification, refusal and default assertion says in plain words what was
expected and what happened, for example "a carrier change that costs the customer more must be a
substantial change, but it was classified minor"."""


def plainly(expected: str, happened: object) -> str:
    """`<what was expected>, but <what happened>`: one plain sentence for a failed assertion."""
    return f"{expected}, but {happened}"
