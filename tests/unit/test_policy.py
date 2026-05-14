from __future__ import annotations

import pytest

from homekit.core.policy import Policy, operation_for_entity
from homekit.exceptions import PolicyBlockedError


def test_default_allow_passes() -> None:
    policy = Policy({"lock.unlock": "confirmation_required"})
    decision = policy.enforce("cover.open")
    assert decision.allowed
    assert decision.policy == "allow"


def test_disabled_blocks_even_with_token() -> None:
    policy = Policy({"garage.open": "disabled"})
    with pytest.raises(PolicyBlockedError):
        policy.enforce("garage.open", confirmation_token="x")


def test_confirmation_required_without_token_blocks() -> None:
    policy = Policy({"lock.unlock": "confirmation_required"})
    with pytest.raises(PolicyBlockedError):
        policy.enforce("lock.unlock")


def test_confirmation_required_with_token_passes() -> None:
    policy = Policy({"lock.unlock": "confirmation_required"})
    decision = policy.enforce("lock.unlock", confirmation_token="ok")
    assert decision.allowed
    assert decision.policy == "confirmation_required"


def test_evaluate_does_not_raise() -> None:
    policy = Policy({"garage.open": "disabled"})
    decision = policy.evaluate("garage.open")
    assert not decision.allowed


def test_operation_for_entity_combines_parts() -> None:
    assert operation_for_entity("lock", "unlock") == "lock.unlock"
