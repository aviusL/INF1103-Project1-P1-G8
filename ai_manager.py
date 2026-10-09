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
    "duration_min": (int, float),
    "intensity": (str,),
    "target_hr_bpm": (int, float),
    "target_pace_min_per_km": (int, float, type(None)),
    "warmup": (str,),
    "main_set": (str,),
    "cooldown": (str,),
    "rationale": (str,),
    "risk_level": (str,),
}

SYSTEM_PROMPT = """You are an exercise-planning assistant inside a fitness coaching app.
You receive a JSON payload with the user's profile, their recent session history
(planned vs actual: pace, heart rate, RPE, pain/discomfort, comments), and optionally
a reason the user rejected a previous suggestion and/or safety-rule violations to fix.

Design the user's NEXT single exercise session. Adapt to how recent sessions actually went:
progress gradually when sessions felt easy, ease off after high RPE, pain or discomfort,
and respect any health concerns and the user's goal.

Reply with ONLY one JSON object, no markdown, no commentary, exactly this shape:
{
  "exercise_type": "string, e.g. easy run, intervals, cycling, strength",
  "duration_min": number,
  "intensity": "low" | "moderate" | "high",
  "target_hr_bpm": number,
  "target_pace_min_per_km": number or null,
  "warmup": "string",
  "main_set": "string",
  "cooldown": "string",
  "rationale": "string, 1-3 sentences explaining why this session fits the user now",
  "risk_level": "low" | "medium" | "high"
}"""


def build_user_prompt(profile, history, user_feedback=None, rule_feedback=None):  #what claude is being asked
    payload = {
        "profile": profile, #age, weight, fitness level, health concerns, goals
        "recent_sessions": history, #last 5 sessions, planned vs actual: pace, heart rate, RPE, pain/discomfort, comments
        "user_rejection_reason": user_feedback, #none unless someone rejected a previous suggestion, then it's the reason they gave
        "safety_rule_violations_to_fix": rule_feedback, #none unless logic layer rejetcs the prev draft
    }
    return "Plan the next session for this user:\n" + json.dumps(payload, indent=2)


def extract_json_text(text):
    #strip code fences / surrounding chatter and return the json substring.
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start:end + 1]


def validate_schema(data):
    #checks the structure of the json object only, returns an error string or None.
    if not isinstance(data, dict):
        return "response is not a JSON object"
    for field, types in RESPONSE_SCHEMA.items():
        if field not in data:
            return "missing field: " + field
        value = data[field]
        if isinstance(value, bool) or not isinstance(value, types):
            return "wrong type for field: " + field
    if data["intensity"] not in INTENSITY_VALUES:
        return "intensity must be one of {}".format(INTENSITY_VALUES)
    if data["risk_level"] not in RISK_VALUES:
        return "risk_level must be one of {}".format(RISK_VALUES)
    return None
