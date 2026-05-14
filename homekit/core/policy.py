"""Policy gate for ``dangerous_operations`` defined in config."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from homekit.exceptions import PolicyBlockedError

logger = logging.getLogger(__name__)

PolicyValue = Literal["allow", "confirmation_required", "disabled"]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    operation: str
    policy: PolicyValue
    requires_confirmation: bool
    allowed: bool


class Policy:
    """Evaluate dangerous operations against the configured policy table."""

    def __init__(self, rules: dict[str, PolicyValue]) -> None:
        self._rules: dict[str, PolicyValue] = dict(rules)

    def lookup(self, operation: str) -> PolicyValue:
        return self._rules.get(operation, "allow")

    def evaluate(
        self,
        operation: str,
        *,
        confirmation_token: str | None = None,
    ) -> PolicyDecision:
        policy = self.lookup(operation)
        if policy == "disabled":
            return PolicyDecision(operation, policy, False, allowed=False)
        if policy == "confirmation_required":
            allowed = bool(confirmation_token)
            return PolicyDecision(operation, policy, True, allowed=allowed)
        return PolicyDecision(operation, policy, False, allowed=True)

    def enforce(
        self,
        operation: str,
        *,
        confirmation_token: str | None = None,
    ) -> PolicyDecision:
        decision = self.evaluate(operation, confirmation_token=confirmation_token)
        if not decision.allowed:
            if decision.policy == "disabled":
                raise PolicyBlockedError(
                    f"Operation {operation!r} is disabled by config"
                )
            raise PolicyBlockedError(
                f"Operation {operation!r} requires confirmation_token"
            )
        if decision.policy != "allow":
            logger.info(
                "policy.audit operation=%s policy=%s confirmed=%s",
                operation,
                decision.policy,
                bool(confirmation_token),
            )
        return decision


def operation_for_entity(domain: str, action: str) -> str:
    """Map an entity domain + verb onto the policy table's operation key."""
    return f"{domain}.{action}"
