"""CMP.5 part 2: the pure rules of a DSA article 16 notice (content, status workflow)."""

from apps.compliance import engine


def codes(**overrides: object) -> list[str]:
    values: dict[str, object] = {
        "explanation": "This listing offers an item that is illegal to sell.",
        "url": "https://shop.example.com/products/thing/",
        "category": "general",
        "notifier_name": "Ada Reporter",
        "notifier_email": "ada@example.org",
        "good_faith": True,
    }
    values.update(overrides)
    return [p.code for p in engine.notice_problems(**values)]  # type: ignore[arg-type]


def test_a_complete_notice_has_no_problem() -> None:
    assert codes() == []


def test_explanation_url_and_good_faith_are_required() -> None:
    assert codes(explanation="  ") == ["explanation"]
    assert codes(url="") == ["url"]
    assert codes(good_faith=False) == ["good_faith"]


def test_the_location_must_be_an_http_url() -> None:
    assert codes(url="javascript:alert(1)") == ["url"]
    assert codes(url="ftp://example.com/x") == ["url"]
    assert codes(url="http://example.com/x") == []


def test_name_and_email_are_required_except_for_child_abuse_reports() -> None:
    assert codes(notifier_name="", notifier_email="") == ["notifier_name", "notifier_email"]
    assert codes(category="child_abuse", notifier_name="", notifier_email="") == []


def test_an_email_must_look_like_one() -> None:
    assert codes(notifier_email="not-an-address") == ["notifier_email"]


def test_the_workflow_goes_received_review_then_a_decision() -> None:
    assert engine.notice_transition_allowed("received", "under_review")
    assert engine.notice_transition_allowed("under_review", "action_taken")
    assert engine.notice_transition_allowed("under_review", "no_action")
    assert not engine.notice_transition_allowed("received", "action_taken")
    assert not engine.notice_transition_allowed("received", "no_action")
    assert not engine.notice_transition_allowed("action_taken", "under_review")
    assert not engine.notice_transition_allowed("no_action", "action_taken")
    assert not engine.notice_transition_allowed("under_review", "received")


def test_a_decision_needs_a_reason() -> None:
    assert engine.decision_problems("action_taken", "") == ["reason"]
    assert engine.decision_problems("no_action", "Not illegal under local law.") == []
    assert engine.decision_problems("received", "x") == ["outcome"]
