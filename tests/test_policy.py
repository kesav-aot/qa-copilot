import pytest

from qa_copilot.config import PolicyConfig
from qa_copilot.dsl.schema import Risk, TestPlan
from qa_copilot.policy.engine import ApprovalStore, PolicyEngine, fingerprint


@pytest.fixture
def engine(tmp_path):
    return PolicyEngine(
        PolicyConfig(allowed_environments=["demo"]), ApprovalStore(tmp_path / "approvals")
    )


def plan(steps, environment="demo", risk="low", name="p"):
    return TestPlan.model_validate(
        {"version": 1, "name": name, "environment": environment, "risk": risk, "steps": steps}
    )


READ_ONLY = [
    {"action": "navigate", "path": "/dashboard"},
    {"action": "assert", "kind": "text", "expected": "Dashboard"},
]


def test_read_only_plan_runs_without_approval(engine):
    d = engine.evaluate(plan(READ_ONLY))
    assert d.risk is Risk.LOW
    assert d.can_execute and not d.requires_approval


def test_destructive_verb_raises_risk_and_demands_approval(engine):
    d = engine.evaluate(
        plan([{"action": "click", "target": {"testid": "disable-user-1"}}, *READ_ONLY])
    )
    assert d.risk is Risk.MEDIUM
    assert d.requires_approval and not d.can_execute


def test_api_delete_is_high_risk(engine):
    d = engine.evaluate(plan([{"action": "api_request", "method": "DELETE", "path": "/api/users/1"}]))
    assert d.risk is Risk.HIGH


def test_declared_risk_cannot_be_lowered_below_inferred(engine):
    d = engine.evaluate(plan([{"action": "api_request", "method": "DELETE", "path": "/x"}], risk="low"))
    assert d.risk is Risk.HIGH


def test_declared_risk_is_honoured_when_higher_than_inferred(engine):
    assert engine.evaluate(plan(READ_ONLY, risk="high")).risk is Risk.HIGH


def test_blocked_environment_is_never_allowed(engine):
    d = engine.evaluate(plan(READ_ONLY, environment="production"))
    assert not d.allowed and not d.can_execute
    assert any("blocked" in v for v in d.violations)


def test_environment_outside_the_allow_list_is_refused(engine):
    d = engine.evaluate(plan(READ_ONLY, environment="qa2"))
    assert not d.allowed


def test_human_approval_unblocks_exactly_one_plan(engine):
    p = plan([{"action": "click", "target": {"testid": "disable-user-1"}}, *READ_ONLY])
    d = engine.evaluate(p)
    assert not d.can_execute

    engine.approvals.approve(d.fingerprint, approver="qa-lead")
    assert engine.evaluate(p).can_execute

    # Any edit changes the fingerprint, so the approval no longer applies.
    edited = plan(
        [{"action": "click", "target": {"testid": "disable-user-2"}}, *READ_ONLY]
    )
    assert not engine.evaluate(edited).can_execute


def test_fingerprint_is_stable_and_order_independent_of_dict_keys():
    a = plan(READ_ONLY)
    b = TestPlan.model_validate(
        {"steps": READ_ONLY, "environment": "demo", "name": "p", "risk": "low", "version": 1}
    )
    assert fingerprint(a) == fingerprint(b)


def test_plan_without_assertions_warns(engine):
    d = engine.evaluate(plan([{"action": "navigate", "path": "/x"}]))
    assert any("no assertions" in w for w in d.warnings)


def test_an_assertion_mentioning_a_destructive_word_is_not_destructive(engine):
    """'Check the Delete button is not shown' is the authorization test you most
    want running unattended — it must not be gated behind an approval."""
    plan_ = plan(
        [
            {"action": "navigate", "path": "/users"},
            {
                "action": "assert",
                "kind": "not_visible",
                "target": {"describe": "the Delete button"},
            },
        ]
    )
    decision = engine.evaluate(plan_)
    assert decision.risk is Risk.LOW
    assert decision.can_execute


def test_clicking_something_destructive_is_still_flagged(engine):
    decision = engine.evaluate(
        plan(
            [
                {"action": "click", "target": {"describe": "the Delete button"}},
                {"action": "assert", "kind": "text", "expected": "gone"},
            ]
        )
    )
    assert decision.risk is Risk.MEDIUM


def test_taking_a_screenshot_named_after_a_deletion_is_not_destructive(engine):
    decision = engine.evaluate(
        plan(
            [
                {"action": "screenshot", "name": "after-delete"},
                {"action": "assert", "kind": "text", "expected": "gone"},
            ]
        )
    )
    assert decision.risk is Risk.LOW
