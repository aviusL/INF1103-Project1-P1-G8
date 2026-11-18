import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Optional

try:
    import anthropic
except ImportError:  # handled at call time so the app still starts without the SDK
    anthropic = None

logger = logging.getLogger("ai_manager")

INTENSITY_VALUES = ("low", "moderate", "high")
RISK_VALUES = ("low", "medium", "high")

# Claude models the user can pick in Settings: (model id, short name, description).
DEFAULT_MODEL = "claude-sonnet-5-5"
MODEL_CHOICES = (
    ("claude-haiku-5-5", "Haiku", "Fastest and lowest cost"),
    ("claude-sonnet-5-5", "Sonnet", "Balanced (default)"),
    ("claude-opus-5-5", "Opus", "Most capable, slower and pricier"),
)
MODEL_IDS = tuple(model_id for model_id, _, _ in MODEL_CHOICES)
NO_KEY_MESSAGE = "no Claude API key: add one in Settings or set ANTHROPIC_API_KEY"

# What an HTTP error from the API means for the user.
STATUS_MESSAGES = {
    400: "the AI service rejected the request (HTTP 400)",
    401: "the AI service rejected the API key (HTTP 401): check the key in Settings",
    403: "this API key is not allowed to use the selected model (HTTP 403)",
    404: "the selected model is not available to this API key (HTTP 404)",
    429: "the AI service rate limit was reached (HTTP 429): wait a minute and try again",
    529: "the AI service is overloaded (HTTP 529): try again shortly",
}


def status_message(code):
    return STATUS_MESSAGES.get(code, "the AI service returned an error (HTTP {})".format(code))


# --------------------------------------------------------------------------- #
# configuration and value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AIConfig:
    #settings for the AI layer. Immutable, so one config can be shared safely
    model: str = DEFAULT_MODEL
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
class SessionFeedbackRequest:
    #everything the AI needs to review one finished session
    _PROFILE_FIELDS = ("age", "sex", "fitness_goal", "fitness_level", "health_concerns", "preferred_activities")

    profile: dict
    session: dict        # planned + actual for a logged session, or the stats of an imported one
    review: dict         # LogicManager's plan-vs-actual comparison (the facts to write about)

    def to_payload(self):
        return {
            "profile": {field: self.profile.get(field) for field in self._PROFILE_FIELDS},
            "session": self.session,
            "app_review": self.review,
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
    def for_session_feedback(cls):
        #the shape SessionFeedbackPromptBuilder asks Claude to reply with.
        return cls([
            NonEmptyTextField("summary"),
            StringListField("went_well"),
            StringListField("to_improve"),
            NonEmptyTextField("next_time"),
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
and session_duration_min), their recent session history (planned vs actual: pace in min/km, or
avg_speed_kmh for cycling, heart rate, RPE, pain/discomfort, comments), the weekly_plan_target for this session, and optionally
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

class SessionFeedbackPromptBuilder(PromptBuilder):
    """Owns all prompt wording for the coach's review of one finished session."""

    _SYSTEM_PROMPT = """You are the coach inside a fitness coaching app, reviewing ONE finished session for the user.
You receive a JSON payload with the user's profile, the session and an app_review calculated by the app.

If session.planned is present, the session was planned by the app and then logged by the user: compare
what was planned (duration, intensity, target heart rate, target pace, main set) with what was actually
done (duration, average heart rate, pace, or average speed in km/h for cycling, RPE, pain, the user's comments). app_review.adherence and
app_review.checks already state how each number compared with the plan; use them and do not contradict them.
If session.source is present instead, it is past training imported from another app with no plan:
review it on its own against the user's goal and level.

Write in plain, encouraging language addressed to the user as "you". Base every statement on the data given
and never invent numbers. If pain was reported or health concerns are listed, advise caution and suggest
seeing a professional if pain continues; never diagnose. Keep the whole review under 120 words.

Reply with ONLY one JSON object, no markdown, no commentary, exactly this shape:
{
  "summary": "string, 2-3 sentences: how the session went, compared with the plan when there is one",
  "went_well": ["string", ...],
  "to_improve": ["string", ...],
  "next_time": "string, one concrete suggestion for the next similar session"
}
went_well and to_improve hold 1-3 short points each (to_improve may be empty if nothing needs changing)."""

    @property
    def system_prompt(self):
        return self._SYSTEM_PROMPT

    @property
    def intro(self):
        return "Review this session for the user:"

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

    def configure(self, api_key=None, model=None):
        """Use a new API key and/or model from now on. Clients without either ignore this."""

    def verify(self, api_key=None, model=None):
        """Check that requests would work, without planning anything. Returns (ok, message).
        api_key / model, if given, are tried for this check only. Never raises."""
        return True, "this AI client needs no connection check"


class ClaudeClient(LLMClient):
    #LLMClient backed by the Anthropic Messages API.

    VERIFY_TIMEOUT_SECONDS = 15.0

    def __init__(self, config, api_key=None):
        self._config = config
        self._api_key = api_key or None   # None: fall back to ANTHROPIC_API_KEY
        self._client = None               # built on the first call, then reused
        self._client_key = None           # the key self._client was built with

    @property
    def model(self):
        return self._config.model

    def configure(self, api_key=None, model=None):
        self._api_key = api_key or None
        if model:
            self._config = replace(self._config, model=model)
        self._client = None               # rebuilt with the new key on the next request

    def _key(self, override=None):
        return override or self._api_key or os.environ.get("ANTHROPIC_API_KEY") or None

    def complete(self, system_prompt, user_prompt):
        key = self._key()
        if not key:
            return None, NO_KEY_MESSAGE
        if anthropic is None:
            return None, "the 'anthropic' package is not installed"
        try:
            response = self._sdk_client(key).messages.create(
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
            return None, status_message(exc.status_code)
        except Exception as exc:  # last resort: the app must never crash on an AI failure
            logger.exception("Unexpected AI error: %s", exc)
            return None, "unexpected AI error"

    def verify(self, api_key=None, model=None):
        """Look up the model with the key: free, and proves both the key and model access."""
        key = self._key(api_key)
        model = model or self._config.model
        if not key:
            return False, NO_KEY_MESSAGE
        if anthropic is None:
            return False, "the 'anthropic' package is not installed"
        try:
            client = anthropic.Anthropic(api_key=key, timeout=self.VERIFY_TIMEOUT_SECONDS, max_retries=0)
            models = getattr(client, "models", None)
            if models is not None and hasattr(models, "retrieve"):
                models.retrieve(model)
            else:  # older SDKs without the Models API: smallest possible request
                client.messages.create(model=model, max_tokens=1, messages=[{"role": "user", "content": "hi"}])
            return True, "connected: {} is ready to use".format(model)
        except anthropic.APITimeoutError:
            return False, "the AI service timed out"
        except anthropic.APIConnectionError as exc:
            logger.error("Connection check failed: %s", exc)
            return False, "could not connect to the AI service: check your internet connection"
        except anthropic.APIStatusError as exc:
            logger.error("Connection check: status %s", exc.status_code)
            return False, status_message(exc.status_code)
        except Exception as exc:
            logger.exception("Unexpected error during connection check: %s", exc)
            return False, "unexpected error while checking the connection"

    def _sdk_client(self, key):
        if self._client is None or self._client_key != key:
            self._client = anthropic.Anthropic(api_key=key, timeout=self._config.timeout_seconds,
                                               max_retries=self._config.sdk_max_retries)
            self._client_key = key
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
        self._feedback_prompts = SessionFeedbackPromptBuilder()
        self._feedback_parser = ResponseParser(ResponseSchema.for_session_feedback())

    @property
    def model(self):
        return self._config.model

    def configure(self, api_key=None, model=None):
        """Apply the API key and model chosen in Settings (empty key: use ANTHROPIC_API_KEY)."""
        if model:
            self._config = replace(self._config, model=model)
        self._client.configure(api_key=api_key or None, model=self._config.model)

    def verify_connection(self, api_key=None, model=None):
        """(ok, message): would a request with this key and model work? Blocking network call."""
        return self._client.verify(api_key=api_key or None, model=model or self._config.model)

    def request_session(self, profile, history, user_feedback=None, rule_feedback=None, weekly_target=None):
        """Ask the model for the next session, re-asking if the reply is malformed."""
        request = SessionRequest(profile, history, user_feedback, rule_feedback, weekly_target)
        return self._ask(self._session_prompts, self._session_parser, request)

    def request_weekly_note(self, profile, report):
        """Ask the model for a short coaching note on a weekly progress report (a plain dict)."""
        return self._ask(self._note_prompts, self._note_parser, WeeklyNoteRequest(profile, report))

    def request_session_feedback(self, profile, session, review):
        """Ask the model to review one finished session against its plan (review: a SessionReview dict)."""
        return self._ask(self._feedback_prompts, self._feedback_parser,
                         SessionFeedbackRequest(profile, session, review))

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
