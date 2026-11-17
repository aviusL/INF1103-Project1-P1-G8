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
class WeeklyNoteRequest:
    #everything the AI needs to write a coaching note on one week of training
    _PROFILE_FIELDS = ("fitness_goal", "fitness_level", "health_concerns", "days_per_week", "preferred_activities")

    profile: dict
    report: dict

    def to_payload(self):
        return {
            "profile": {field: self.profile.get(field) for field in self._PROFILE_FIELDS},
            "weekly_report": self.report,
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
        pass

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


class NonEmptyTextField(FieldSpec):
    #string that must contain some text; a blank coaching note is useless to the user

    def __init__(self, name):
        super().__init__(name, (str,))

    def check_value(self, value):
        if not value.strip():
            return "{} must not be empty".format(self._name)
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

    @classmethod
    def for_weekly_note(cls):
        #the shape WeeklyNotePromptBuilder asks Claude to reply with.
        return cls([
            NonEmptyTextField("summary"),
            NonEmptyTextField("focus_next_week"),
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

# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
class PromptBuilder(ABC):
    """Base for every prompt the AI layer sends.

    Subclasses supply the wording; this class supplies the shared JSON rendering and
    the retry message, so AIManager can drive any of them the same way.
    """

    @property
    @abstractmethod
    def system_prompt(self):
        #instructions sent as the system prompt.
        pass

    @property
    @abstractmethod
    def intro(self):
        #first line of the user prompt, placed above the JSON payload.
        pass

    def build(self, request):
        return self.intro + "\n" + json.dumps(request.to_payload(), indent=2)

    def build_retry(self, base_prompt, schema_error):
        """Re-ask after a malformed reply, telling the model what was wrong."""
        return (base_prompt + "\n\nYour previous reply was rejected: " + schema_error +
                ". Reply with ONLY the JSON object in the required shape.")


class SessionPromptBuilder(PromptBuilder):
    """Owns all prompt wording for planning a session."""

    _SYSTEM_PROMPT = """You are an exercise-planning assistant inside a fitness coaching app.
You receive a JSON payload with the user's profile (including sex, equipment, days_per_week
and session_duration_min), their recent session history (planned vs actual: pace, heart rate,
RPE, pain/discomfort, comments), the weekly_plan_target for this session, and optionally
a reason the user rejected a previous suggestion and/or safety-rule violations to fix.

Design the user's NEXT single exercise session. Adapt to how recent sessions actually went:
progress gradually when sessions felt easy, ease off after high RPE, pain or discomfort,
and respect any health concerns and the user's goal.

Hard requirements for this session:
- intensity must not be above weekly_plan_target.intensity (it is this session's place in the
  user's week; the user trains days_per_week days, with easier days between harder ones).
- duration_min must not exceed profile.session_duration_min; use most of that time when sensible.
- Use ONLY equipment listed in profile.equipment, plus bodyweight exercises. An empty list means
  bodyweight only. Put the exact equipment names you use in equipment_used (use
  ["bodyweight"] if none).
- activity must be exactly one of profile.preferred_activities (if that list is missing, choose
  any suitable activity). Rotate through the user's activities across the week rather than
  repeating the same one every session, unless they chose only one. exercise_type describes
  the specific workout, for example "tempo run" or "interval swim".
- Set target_pace_min_per_km only for running or walking; use null for every other activity.

Reply with ONLY one JSON object, no markdown, no commentary, exactly this shape:
{
  "exercise_type": "string, e.g. easy run, interval swim, hill ride, strength circuit",
  "activity": "string, exactly one of profile.preferred_activities",
  "duration_min": number,
  "intensity": "low" | "moderate" | "high",
  "target_hr_bpm": number,
  "target_pace_min_per_km": number or null,
  "equipment_used": ["string", ...],
  "warmup": "string",
  "main_set": "string",
  "cooldown": "string",
  "rationale": "string, 1-3 sentences explaining why this session fits the user now",
  "risk_level": "low" | "medium" | "high"
}"""

    @property
    def system_prompt(self):
        return self._SYSTEM_PROMPT

    @property
    def intro(self):
        return "Plan the next session for this user:"


class WeeklyNotePromptBuilder(PromptBuilder):
    """Owns all prompt wording for the coaching note in the weekly progress report."""

    _SYSTEM_PROMPT = """You are the coach inside a fitness coaching app, writing a short note about the user's training week.
You receive a JSON payload with the user's profile (goal, fitness level, health concerns, days_per_week,
preferred activities) and a weekly_report calculated by the app: this_week and last_week totals
(sessions done vs planned, minutes done vs planned, average RPE and heart rate, sessions with pain,
intensity mix, activities) and the app's own observations.

Write in plain, encouraging language addressed to the user as "you". Base every statement on the numbers
given and never invent sessions or results. Relate the week to the user's goal. If pain was reported or
health concerns are listed, advise caution and suggest seeing a professional if pain continues; never
diagnose. Keep the whole note under 90 words.

Reply with ONLY one JSON object, no markdown, no commentary, exactly this shape:
{
  "summary": "string, 2-3 sentences on how this week went compared with last week",
  "focus_next_week": "string, 1-2 sentences with one concrete focus for next week"
}"""

    @property
    def system_prompt(self):
        return self._SYSTEM_PROMPT

    @property
    def intro(self):
        return "Write the weekly coaching note for this user:"

# --------------------------------------------------------------------------- #
# Model clients
# --------------------------------------------------------------------------- #
class LLMClient(ABC):
    """Interface for any chat model the AI layer can call.

    Contract for every implementation: return (text, error_message) with exactly one
    of them None, and never raise - the app must not crash on an AI failure.
    """

    @abstractmethod
    def complete(self, system_prompt, user_prompt):
        #Send one request and return (text, error_message).
        pass


class ClaudeClient(LLMClient):
    #LLMClient backed by the Anthropic Messages API.

    def __init__(self, config):
        self._config = config
        self._client = None  # built on the first call, then reused

    def complete(self, system_prompt, user_prompt):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None, "ANTHROPIC_API_KEY is not set"
        if anthropic is None:
            return None, "the 'anthropic' package is not installed"
        try:
            response = self._sdk_client().messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return "".join(b.text for b in response.content if b.type == "text"), None
        except anthropic.APITimeoutError:  # subclass of APIConnectionError, so it must come first
            logger.error("Claude API timed out")
            return None, "the AI service timed out"
        except anthropic.APIConnectionError as exc:
            logger.error("Claude API connection failure: %s", exc)
            return None, "could not connect to the AI service"
        except anthropic.APIStatusError as exc:
            logger.error("Claude API status error %s: %s", exc.status_code, exc)
            return None, "the AI service returned an error (HTTP {})".format(exc.status_code)
        except Exception as exc:  # last resort: the app must never crash on an AI failure
            logger.exception("Unexpected AI error: %s", exc)
            return None, "unexpected AI error"

    def _sdk_client(self):
        if self._client is None:
            self._client = anthropic.Anthropic(timeout=self._config.timeout_seconds,
                                               max_retries=self._config.sdk_max_retries)
        return self._client

# --------------------------------------------------------------------------- #
# Facade
# --------------------------------------------------------------------------- #
class AIManager:
    """Entry point for the controller: builds the prompt, calls the model, returns a validated AIResult.

    Composition: the prompt builders and parsers are created here and live only as long as the manager.
    Association: the LLMClient is handed in from outside and only used, so tests can pass a stub
    and any LLMClient implementation works without changes here.
    """

    def __init__(self, client=None, config=None):
        self._config = config or AIConfig.from_env()
        self._client = client if client is not None else ClaudeClient(self._config)
        if not isinstance(self._client, LLMClient):
            raise TypeError("client must implement LLMClient")
        self._session_prompts = SessionPromptBuilder()
        self._session_parser = ResponseParser(ResponseSchema.for_session_plan())
        self._note_prompts = WeeklyNotePromptBuilder()
        self._note_parser = ResponseParser(ResponseSchema.for_weekly_note())

    def request_session(self, profile, history, user_feedback=None, rule_feedback=None, weekly_target=None):
        """Ask the model for the next session, re-asking if the reply is malformed."""
        request = SessionRequest(profile, history, user_feedback, rule_feedback, weekly_target)
        return self._ask(self._session_prompts, self._session_parser, request)

    def request_weekly_note(self, profile, report):
        """Ask the model for a short coaching note on a weekly progress report (a plain dict)."""
        return self._ask(self._note_prompts, self._note_parser, WeeklyNoteRequest(profile, report))

    def _ask(self, prompts, parser, request):
        """Shared request loop for any PromptBuilder: call the model, re-ask once if the reply is malformed."""
        base_prompt = prompts.build(request)
        prompt = base_prompt
        last_error = "unknown error"
        for attempt in range(1, self._config.max_schema_attempts + 1):
            text, api_error = self._client.complete(prompts.system_prompt, prompt)
            if api_error:
                return AIResult.failure(api_error)
            data, schema_error = parser.parse(text)
            if schema_error is None:
                return AIResult.success(data)
            last_error = schema_error
            logger.warning("Malformed AI output (attempt %d): %s", attempt, schema_error)
            prompt = prompts.build_retry(base_prompt, schema_error)
        return AIResult.failure("AI returned malformed output: " + last_error)
