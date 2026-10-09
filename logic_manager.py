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
