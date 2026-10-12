"""ai_manager.py - AI layer: talks to Claude only. No domain/business rules, no console output."""
import json
import logging
import os
import re

logger = logging.getLogger("ai_manager")

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
API_TIMEOUT_SECONDS = 30.0
MAX_SCHEMA_ATTEMPTS = 2

INTENSITY_VALUES = ("low", "moderate", "high")
RISK_VALUES = ("low", "medium", "high")

# field name -> allowed python types
RESPONSE_SCHEMA = {
    "exercise_type": (str,),
    "activity": (str,),
    "duration_min": (int, float),
    "intensity": (str,),
    "target_hr_bpm": (int, float),
    "target_pace_min_per_km": (int, float, type(None)),
    "equipment_used": (list,),
    "warmup": (str,),
    "main_set": (str,),
    "cooldown": (str,),
    "rationale": (str,),
    "risk_level": (str,),
}

SYSTEM_PROMPT = """You are an exercise-planning assistant inside a fitness coaching app.
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


def build_user_prompt(profile, history, user_feedback=None, rule_feedback=None, weekly_target=None):
    payload = {
        "profile": profile,
        "weekly_plan_target": weekly_target,
        "recent_sessions": history,
        "user_rejection_reason": user_feedback,
        "safety_rule_violations_to_fix": rule_feedback,
    }
    return "Plan the next session for this user:\n" + json.dumps(payload, indent=2)


def extract_json_text(text):
    """Strip code fences / surrounding chatter and return the JSON substring."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start:end + 1]


def validate_schema(data):
    """Structural validation only. Returns an error string or None."""
    if not isinstance(data, dict):
        return "response is not a JSON object"
    for field, types in RESPONSE_SCHEMA.items():
        if field not in data:
            return "missing field: " + field
        value = data[field]
        if isinstance(value, bool) or not isinstance(value, types):
            return "wrong type for field: " + field
    if not all(isinstance(item, str) for item in data["equipment_used"]):
        return "equipment_used must be a list of strings"
    if data["intensity"] not in INTENSITY_VALUES:
        return "intensity must be one of {}".format(INTENSITY_VALUES)
    if data["risk_level"] not in RISK_VALUES:
        return "risk_level must be one of {}".format(RISK_VALUES)
    return None


def parse_and_validate_response(text):
    """Return (data, error). Exactly one of them is None."""
    if not isinstance(text, str) or not text.strip():
        return None, "empty response"
    try:
        data = json.loads(extract_json_text(text))
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: {}".format(exc)
    error = validate_schema(data)
    if error:
        return None, error
    return data, None


def call_claude(system_prompt, user_prompt):
    """Single API call. Returns (text, error_message). Never raises."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "ANTHROPIC_API_KEY is not set"
    try:
        import anthropic
    except ImportError:
        return None, "the 'anthropic' package is not installed"
    try:
        client = anthropic.Anthropic(timeout=API_TIMEOUT_SECONDS, max_retries=2)
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return text, None
    except anthropic.APITimeoutError:
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


def request_session(profile, history, user_feedback=None, rule_feedback=None, weekly_target=None):
    """Ask Claude for the next session.
    Returns {"ok": bool, "data": dict|None, "error": str|None}."""
    base_prompt = build_user_prompt(profile, history, user_feedback, rule_feedback, weekly_target)
    prompt = base_prompt
    last_error = "unknown error"
    for attempt in range(1, MAX_SCHEMA_ATTEMPTS + 1):
        text, api_error = call_claude(SYSTEM_PROMPT, prompt)
        if api_error:
            return {"ok": False, "data": None, "error": api_error}
        data, schema_error = parse_and_validate_response(text)
        if schema_error is None:
            return {"ok": True, "data": data, "error": None}
        last_error = schema_error
        logger.warning("Malformed AI output (attempt %d): %s", attempt, schema_error)
        prompt = (base_prompt + "\n\nYour previous reply was rejected: " + schema_error +
                  ". Reply with ONLY the JSON object in the required shape.")
    return {"ok": False, "data": None, "error": "AI returned malformed output: " + last_error}
