import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

try:
    import anthropic
except ImportError:  # handled at call time so the app still starts without the SDK
    anthropic = None

logger = logging.getLogger("ai_manager")

INTENSITY_VALUES = ("low", "moderate", "high")
RISK_VALUES = ("low", "medium", "high")


# --------------------------------------------------------------------------- #
# configuration and value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AIConfig:
    """Settings for the AI layer. Immutable, so one config can be shared safely."""
    model: str = "claude-sonnet-5-5"
    timeout_seconds: float = 30.0
    max_tokens: int = 1000
    sdk_max_retries: int = 2
    max_schema_attempts: int = 2

    @classmethod
    def from_env(cls):
        return cls(model=os.environ.get("CLAUDE_MODEL", cls.model))


@dataclass(frozen=True)
class SessionRequest:
    #everything the AI needs to plan one session
    profile: dict
    history: list
    user_feedback: Optional[str] = None
    rule_feedback: Optional[list] = None
    weekly_target: Optional[dict] = None

    def to_payload(self):
        return {
            "profile": self.profile,
            "weekly_plan_target": self.weekly_target,
            "recent_sessions": self.history,
            "user_rejection_reason": self.user_feedback,
            "safety_rule_violations_to_fix": self.rule_feedback,
        }


@dataclass(frozen=True)
class AIResult:
    #outcome of one ai request. when ok is True data is set, otherwise error is.
    ok: bool
    data: Optional[dict] = None
    error: Optional[str] = None

    @classmethod
    def success(cls, data):
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error):
        return cls(ok=False, error=error)

    def to_dict(self):
        return {"ok": self.ok, "data": self.data, "error": self.error}


# --------------------------------------------------------------------------- #
# Response schema: one polymorphic FieldSpec per expected JSON field
# --------------------------------------------------------------------------- #
class FieldSpec(ABC):
    #abstract description of one field in the ai JSON reply.

    def __init__(self, name, types):
        self._name = name
        self._types = tuple(types)

    @property
    def name(self):
        return self._name

    def check_type(self, data):
        #return an error if the field is missing or has the wrong type, else None.
        if self._name not in data:
            return "missing field: " + self._name
        value = data[self._name]
        if isinstance(value, bool) or not isinstance(value, self._types):  # True is not a number
            return "wrong type for field: " + self._name
        return None

    @abstractmethod
    def check_value(self, value):
        #return an error if a correctly typed value is still not allowed, else None.


class TypedField(FieldSpec):
    #any value of the right type is accepted.

    def check_value(self, value):
        return None
