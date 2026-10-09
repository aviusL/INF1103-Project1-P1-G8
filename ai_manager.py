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
