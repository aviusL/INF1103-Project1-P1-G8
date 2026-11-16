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
