"""logic_manager.py - Logic layer: business rules on user input and AI output. No console output, no I/O."""

HR_CEILING_FRACTION = 0.90      # target HR may not exceed 90% of max HR
HR_FLOOR_FRACTION = 0.40        # below 40% of max HR is suspiciously easy -> flag
MIN_DURATION_MIN = 10
MAX_DURATION_MIN = 120
BEGINNER_MAX_DURATION_MIN = 60
MIN_PACE = 3.0                  # min/km, faster is not humanly sustainable for our users
MAX_PACE = 15.0
PROGRESSION_LIMIT = 1.30        # max +30% duration versus the previous session
HIGH_RPE = 8
NO_CONCERN_WORDS = ("", "none", "no", "nil", "n/a", "na")
INTENSITY_RANK = {"low": 0, "moderate": 1, "high": 2}
DEFAULT_DAYS_PER_WEEK = 3       # used for profiles saved before the weekly plan existed

# equipment that unlocks an activity: the user needs at least one of the items.
# Activities not listed here (running, walking, yoga, ...) need nothing.
ACTIVITY_EQUIPMENT = {
    "swimming": ("swimming pool",),
    "cycling": ("bicycle", "stationary bike"),
    "rowing": ("rowing machine",),
}

# intensity of each training session in a week, by how many days the user can train.
# One hard day, then easier days, so recovery is built into the week.
WEEKLY_PATTERNS = {
    1: ("moderate",),
    2: ("high", "low"),
    3: ("high", "low", "moderate"),
    4: ("high", "low", "moderate", "low"),
    5: ("high", "low", "moderate", "low", "moderate"),
    6: ("high", "low", "moderate", "low", "moderate", "low"),
    7: ("high", "low", "moderate", "low", "moderate", "low", "low"),
}

def calculate_max_hr(age):
    return 220 - age


def has_health_concerns(profile):
    return str(profile.get("health_concerns", "")).strip().lower() not in NO_CONCERN_WORDS


def last_session(history):
    return history[-1] if history else None


def last_session_was_hard(history):
    """True if the previous session had RPE >= 8 or any pain/discomfort reported."""
    last = last_session(history)
    if not last:
        return False
    actual = last.get("actual", {})
    return actual.get("rpe", 0) >= HIGH_RPE or bool(actual.get("pain_reported", False))

def last_planned_was_high(history):
    last = last_session(history)
    return bool(last) and last.get("planned", {}).get("intensity") == "high"


def available_days(profile):
    """Training days per week (1-7). Older profiles without the field get the default."""
    try:
        days = int(profile.get("days_per_week", DEFAULT_DAYS_PER_WEEK))
    except (TypeError, ValueError):
        days = DEFAULT_DAYS_PER_WEEK
    return min(max(days, 1), 7)


def available_equipment(profile):
    """Lower-case names of the equipment the user owns. Bodyweight is always allowed."""
    owned = {str(item).strip().lower() for item in (profile.get("equipment") or [])}
    owned.add("bodyweight")
    return owned


def available_minutes(profile):
    """Minutes the user can train per session. Older profiles get the maximum."""
    try:
        minutes = int(profile.get("session_duration_min", MAX_DURATION_MIN))
    except (TypeError, ValueError):
        minutes = MAX_DURATION_MIN
    return min(max(minutes, MIN_DURATION_MIN), MAX_DURATION_MIN)


def preferred_activities(profile):
    """The user's chosen activities in lower case, or None for older profiles without the field."""
    chosen = profile.get("preferred_activities")
    if not chosen:
        return None
    return [str(item).strip().lower() for item in chosen]


def missing_equipment_for(activity, profile):
    """Equipment options that would unlock this activity, or [] if the user can already do it."""
    needs = ACTIVITY_EQUIPMENT.get(activity, ())
    if not needs or any(item in available_equipment(profile) for item in needs):
        return []
    return list(needs)


def usable_activities(profile):
    """Chosen activities the user's equipment allows (maybe empty), or None if no choice is stored."""
    chosen = preferred_activities(profile)
    if chosen is None:
        return None
    return [activity for activity in chosen if not missing_equipment_for(activity, profile)]


def weekly_intensity_plan(days):
    """The intensity pattern for a week with this many training days."""
    return WEEKLY_PATTERNS[min(max(days, 1), 7)]


def intensity_cap(profile, history):
    """Highest intensity this user may be given right now, from their situation (not the AI's output)."""
    if (has_health_concerns(profile) or profile.get("fitness_level") == "beginner"
            or last_session_was_hard(history) or last_planned_was_high(history)):
        return "moderate"
    return "high"


def weekly_target(profile, sessions_done, history):
    """Plan this session inside the user's week.
    sessions_done = sessions already logged this week. Returns the target intensity for the
    next session: the weekly pattern slot, lowered when the user's situation calls for it."""
    days = available_days(profile)
    pattern = weekly_intensity_plan(days)
    week_complete = sessions_done >= days
    slot = "low" if week_complete else pattern[sessions_done]
    cap = intensity_cap(profile, history)
    intensity = slot if INTENSITY_RANK[slot] <= INTENSITY_RANK[cap] else cap
    return {
        "intensity": intensity,
        "session_number": sessions_done + 1,
        "sessions_per_week": days,
        "week_complete": week_complete,
        "pattern": list(pattern),
    }


def collect_rejections(session, profile, history, weekly_target=None):
    """Hard rules: any violation means the AI suggestion must be rejected."""
    reasons = []
    max_hr = calculate_max_hr(profile["age"])
    duration = session["duration_min"]
    pace = session["target_pace_min_per_km"]
    intensity = session["intensity"]

    if not MIN_DURATION_MIN <= duration <= MAX_DURATION_MIN:
        reasons.append("duration {} min is outside {}-{} min".format(
            duration, MIN_DURATION_MIN, MAX_DURATION_MIN))
    if session["target_hr_bpm"] > HR_CEILING_FRACTION * max_hr:
        reasons.append("target heart rate {} bpm exceeds 90% of max HR ({:.0f} bpm)".format(
            session["target_hr_bpm"], HR_CEILING_FRACTION * max_hr))
    if pace is not None and not MIN_PACE <= pace <= MAX_PACE:
        reasons.append("target pace {} min/km is not realistic".format(pace))
    if session["risk_level"] == "high":
        reasons.append("the AI itself rated this session as high risk")

    # multi-condition rules combining profile, history and AI output fields
    if has_health_concerns(profile) and intensity == "high":
        reasons.append("high intensity is not allowed when the user has health concerns")
    if profile["fitness_level"] == "beginner" and (
            intensity == "high" or duration > BEGINNER_MAX_DURATION_MIN):
        reasons.append("beginners may not do high intensity or sessions over {} min".format(
            BEGINNER_MAX_DURATION_MIN))
    if last_session_was_hard(history) and intensity == "high":
        reasons.append("high intensity is not allowed right after a session with RPE >= {} or pain".format(
            HIGH_RPE))
    last = last_session(history)
    if last and last.get("planned", {}).get("intensity") == "high" and intensity == "high":
        reasons.append("two high-intensity sessions in a row are not allowed")
    return reasons


def collect_flags(session, profile, history):
    """Soft rules: acceptable but worth showing the user as a warning."""
    flags = []
    max_hr = calculate_max_hr(profile["age"])
    if session["target_hr_bpm"] < HR_FLOOR_FRACTION * max_hr:
        flags.append("target heart rate is very low for a training effect")
    last = last_session(history)
    if last:
        previous = last.get("actual", {}).get("duration_min")
        if previous and session["duration_min"] > PROGRESSION_LIMIT * previous:
            flags.append("duration is more than 30% longer than the previous session")
    if session["risk_level"] == "medium":
        flags.append("the AI rated this session as medium risk")
    return flags


def evaluate_session(session, profile, history):
    """Decide the outcome for an AI-suggested session.
    Returns {"outcome": "accept"|"flag"|"reject", "reasons": [...], "score": 0-100}."""
    rejections = collect_rejections(session, profile, history)
    if rejections:
        return {"outcome": "reject", "reasons": rejections, "score": 0}
    flags = collect_flags(session, profile, history)
    score = max(0, 100 - 20 * len(flags))
    return {"outcome": "flag" if flags else "accept", "reasons": flags, "score": score}


def validate_session_log(log, profile):
    """Business-rule check of what the user reports. Returns a list of error strings."""
    errors = []
    max_hr = calculate_max_hr(profile["age"])
    pace = log.get("avg_pace_min_per_km")
    if pace is not None and pace < MIN_PACE:
        errors.append("an average pace of {} min/km is not realistic".format(pace))
    if log["avg_hr_bpm"] > max_hr + 10:
        errors.append("average heart rate {} bpm is far above your estimated max ({})".format(
            log["avg_hr_bpm"], max_hr))
    return errors
