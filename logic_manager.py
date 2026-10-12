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
