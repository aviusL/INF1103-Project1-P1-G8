"""Logic layer: basic rules and helper functions for running sessions."""

HR_CEILING_FRACTION = 0.90
HR_FLOOR_FRACTION = 0.40
MIN_DURATION_MIN = 10
MAX_DURATION_MIN = 120
BEGINNER_MAX_DURATION_MIN = 60
MIN_PACE = 3.0
MAX_PACE = 15.0
PROGRESSION_LIMIT = 1.30
HIGH_RPE = 8
NO_CONCERN_WORDS = ("", "none", "no", "nil", "n/a", "na")


def calculate_max_hr(age):
    """Estimate maximum heart rate from age."""
    return 220 - age


def has_health_concerns(profile):
    """Check whether the user reported any health concerns."""
    return str(profile.get("health_concerns", "")).strip().lower() not in NO_CONCERN_WORDS


def last_session(history):
    """Return the most recent session, or None if history is empty."""
    return history[-1] if history else None


def last_session_was_hard(history):
    """Check if the previous session had high exertion or reported pain."""
    last = last_session(history)
    if not last:
        return False
    actual = last.get("actual", {})
    return actual.get("rpe", 0) >= HIGH_RPE or bool(actual.get("pain_reported", False))


def collect_rejections(session, profile, history):
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
