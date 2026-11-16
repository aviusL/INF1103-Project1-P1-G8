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
    #settings for the AI layer. Immutable, so one config can be shared safely
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

class ChoiceField(FieldSpec):
    # string restricted to a fixed set of choices.

    def __init__(self, name, choices):
        super().__init__(name, (str,))
        self._choices = tuple(choices)

    def check_value(self, value):
        if value not in self._choices:
            return "{} must be one of {}".format(self._name, self._choices)
        return None


class StringListField(FieldSpec):
    #list whose items must all be strings

    def __init__(self, name):
        super().__init__(name, (list,))

    def check_value(self, value):
        if not all(isinstance(item, str) for item in value):
            return "{} must be a list of strings".format(self._name)
        return None


class ResponseSchema:
    #ordered set of FieldSpecs. Add a field or a new kind of check without editing validate().

    def __init__(self, fields):
        self._fields = tuple(fields)  # aggregation: specs are built outside and can be reused
        if not all(isinstance(spec, FieldSpec) for spec in self._fields):
            raise TypeError("all schema fields must implement FieldSpec")

    def validate(self, data):
        """structure validation only. Returns an error string or None.

        Every field's presence and type is checked before any field's value, so a
        missing field is always reported ahead of an out-of-range one.
        """
        if not isinstance(data, dict):
            return "response is not a JSON object"
        for spec in self._fields:
            error = spec.check_type(data)
            if error:
                return error
        for spec in self._fields:
            error = spec.check_value(data[spec.name])
            if error:
                return error
        return None

    @classmethod
    def for_session_plan(cls):
        #the shape SessionPromptBuilder asks Claude to reply with.
        return cls([
            TypedField("exercise_type", (str,)),
            TypedField("activity", (str,)),
            TypedField("duration_min", (int, float)),
            ChoiceField("intensity", INTENSITY_VALUES),
            TypedField("target_hr_bpm", (int, float)),
            TypedField("target_pace_min_per_km", (int, float, type(None))),
            StringListField("equipment_used"),
            TypedField("warmup", (str,)),
            TypedField("main_set", (str,)),
            TypedField("cooldown", (str,)),
            TypedField("rationale", (str,)),
            ChoiceField("risk_level", RISK_VALUES),
        ])


class ResponseParser:
    #turns raw text into a validated dict.

    _CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

    def __init__(self, schema):
        self._schema = schema

    def parse(self, text):
        """Return (data, error). Exactly one of them is None."""
        if not isinstance(text, str) or not text.strip():
            return None, "empty response"
        try:
            data = json.loads(self._extract_json_text(text))
        except json.JSONDecodeError as exc:
            return None, "invalid JSON: {}".format(exc)
        error = self._schema.validate(data)
        if error:
            return None, error
        return data, None

    def _extract_json_text(self, text):
        #Strip code fences / surrounding chatter and return the JSON substring.
        text = self._CODE_FENCE.sub("", text.strip())
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return text
        return text[start:end + 1]
