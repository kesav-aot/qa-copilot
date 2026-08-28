import pytest
from pydantic import ValidationError

from qa_copilot.dsl.schema import Target, TestPlan


def _plan(**steps):
    return {"version": 1, "name": "t", "environment": "demo", "steps": [steps]}


def test_fill_rejects_credential_shaped_values():
    with pytest.raises(ValidationError, match="looks like a credential"):
        TestPlan.model_validate(
            _plan(action="fill", target={"testid": "pw"}, value="password: hunter2")
        )


def test_fill_allows_ordinary_text():
    TestPlan.model_validate(_plan(action="fill", target={"testid": "q"}, value="blue widget"))


def test_authenticate_requires_exactly_one_of_identity_or_capability():
    with pytest.raises(ValidationError):
        TestPlan.model_validate(_plan(action="authenticate"))
    with pytest.raises(ValidationError):
        TestPlan.model_validate(
            _plan(action="authenticate", identity="ADMIN_USER", capability="manage_users")
        )
    TestPlan.model_validate(_plan(action="authenticate", identity="ADMIN_USER"))


def test_fill_secret_only_accepts_a_secret_reference():
    with pytest.raises(ValidationError):
        TestPlan.model_validate(
            _plan(action="fill_secret", target={"testid": "pw"}, secret_ref="hunter2")
        )
    TestPlan.model_validate(
        _plan(action="fill_secret", target={"testid": "pw"}, secret_ref="secret://demo/admin/password")
    )


def test_target_needs_at_least_one_locator():
    with pytest.raises(ValidationError):
        Target()


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        TestPlan.model_validate(_plan(action="navigate", path="/x", username="admin@qa.local"))


def test_assert_kinds_require_their_operand():
    with pytest.raises(ValidationError):
        TestPlan.model_validate(_plan(action="assert", kind="visible"))
    with pytest.raises(ValidationError):
        TestPlan.model_validate(_plan(action="assert", kind="text"))
