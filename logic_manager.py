"""Part 2 OOP fitness business logic. No terminal, storage or API operations.

Input: validated AI session dictionaries, profile dictionaries, and stored history.
Output: weekly targets, decisions and validation errors.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

HR_CEILING_FRACTION = .90
HR_FLOOR_FRACTION = .40
MIN_DURATION_MIN = 10
MAX_DURATION_MIN = 120
BEGINNER_MAX_DURATION_MIN = 60
MIN_PACE = 3.0
MAX_PACE = 15.0
PROGRESSION_LIMIT = 1.30
HIGH_RPE = 8
NO_CONCERN_WORDS = ('', 'none', 'no', 'nil', 'n/a', 'na')
INTENSITY_RANK = {'low': 0, 'moderate': 1, 'high': 2}
DEFAULT_DAYS_PER_WEEK = 3
ACTIVITY_EQUIPMENT = {
    'swimming': ('swimming pool',),
    'cycling': ('bicycle', 'stationary bike'),
    'rowing': ('rowing machine',),
}
WEEKLY_PATTERNS = {
    1: ('moderate',), 2: ('high', 'low'),
    3: ('high', 'low', 'moderate'),
    4: ('high', 'low', 'moderate', 'low'),
    5: ('high', 'low', 'moderate', 'low', 'moderate'),
    6: ('high', 'low', 'moderate', 'low', 'moderate', 'low'),
    7: ('high', 'low', 'moderate', 'low', 'moderate', 'low', 'low'),
}


@dataclass(frozen=True)
class EvaluationContext:
    """One evaluation's inputs; referenced by rules without taking ownership."""
    session: dict
    profile: dict
    history: list
    weekly_target: Optional[dict] = None

    @property
    def max_hr(self):
        return 220 - self.profile['age']

    @property
    def previous(self):
        return self.history[-1] if self.history else None

    @property
    def previous_hard(self):
        if not self.previous:
            return False
        actual = self.previous.get('actual', {})
        return actual.get('rpe', 0) >= HIGH_RPE or bool(actual.get('pain_reported', False))

    @property
    def previous_planned_high(self):
        return bool(self.previous) and self.previous.get('planned', {}).get('intensity') == 'high'

    @property
    def has_health_concerns(self):
        return str(self.profile.get('health_concerns', '')).strip().lower() not in NO_CONCERN_WORDS


class FitnessRule(ABC):
    """Interface and abstract base class for replaceable evaluation policies."""
    @property
    @abstractmethod
    def severity(self):
        """'reject' or 'flag'."""

    @abstractmethod
    def check(self, context: EvaluationContext) -> list[str]:
        """Return reasons; empty list means rule passed."""


class HardRule(FitnessRule):
    @property
    def severity(self):
        return 'reject'


class SoftRule(FitnessRule):
    @property
    def severity(self):
        return 'flag'


class DurationRule(HardRule):
    def check(self, context):
        duration = context.session['duration_min']
        reasons = []
        if not MIN_DURATION_MIN <= duration <= MAX_DURATION_MIN:
            reasons.append(f'duration {duration} min is outside {MIN_DURATION_MIN}-{MAX_DURATION_MIN} min')
        minutes = ProfilePreferences(context.profile).available_minutes()
        if duration > minutes:
            reasons.append(f'duration {duration} min is longer than the {minutes} min the user has available')
        return reasons


class HeartRateRule(HardRule):
    def check(self, context):
        hr = context.session['target_hr_bpm']
        if hr > HR_CEILING_FRACTION * context.max_hr:
            return [f'target heart rate {hr} bpm exceeds 90% of max HR ({HR_CEILING_FRACTION * context.max_hr:.0f} bpm)']
        return []


class PaceRule(HardRule):
    def check(self, context):
        pace = context.session['target_pace_min_per_km']
        return [f'target pace {pace} min/km is not realistic'] if pace is not None and not MIN_PACE <= pace <= MAX_PACE else []


class RiskRule(HardRule):
    def check(self, context):
        return ['the AI itself rated this session as high risk'] if context.session['risk_level'] == 'high' else []


class RecoveryRule(HardRule):
    def check(self, context):
        intensity = context.session['intensity']
        duration = context.session['duration_min']
        reasons = []
        if context.has_health_concerns and intensity == 'high':
            reasons.append('high intensity is not allowed when the user has health concerns')
        if context.profile['fitness_level'] == 'beginner' and (intensity == 'high' or duration > BEGINNER_MAX_DURATION_MIN):
            reasons.append(f'beginners may not do high intensity or sessions over {BEGINNER_MAX_DURATION_MIN} min')
        if context.previous_hard and intensity == 'high':
            reasons.append(f'high intensity is not allowed right after a session with RPE >= {HIGH_RPE} or pain')
        if context.previous_planned_high and intensity == 'high':
            reasons.append('two high-intensity sessions in a row are not allowed')
        return reasons


class EquipmentActivityRule(HardRule):
    def check(self, context):
        preferences = ProfilePreferences(context.profile)
        reasons = []
        usable = preferences.usable_activities()
        activity = str(context.session.get('activity', '')).strip().lower()
        if usable is not None and activity not in usable:
            reasons.append("activity '{}' is not one of the user's usable chosen activities ({})".format(activity, ', '.join(usable) or 'none'))
        owned = preferences.available_equipment()
        used = {str(item).strip().lower() for item in context.session.get('equipment_used', [])}
        missing = sorted(item for item in used if item and item not in owned)
        if missing:
            reasons.append('session uses equipment the user does not have: ' + ', '.join(missing))
        return reasons


class WeeklyIntensityRule(HardRule):
    def check(self, context):
        plan = context.weekly_target
        intensity = context.session['intensity']
        if plan and INTENSITY_RANK[intensity] > INTENSITY_RANK[plan['intensity']]:
            return [f"intensity {intensity} is above this week's plan for session {plan['session_number']} of {plan['sessions_per_week']} (target: {plan['intensity']})"]
        return []


class LowHeartRateRule(SoftRule):
    def check(self, context):
        return ['target heart rate is very low for a training effect'] if context.session['target_hr_bpm'] < HR_FLOOR_FRACTION * context.max_hr else []


class ProgressionRule(SoftRule):
    def check(self, context):
        previous = context.previous
        if previous:
            duration = previous.get('actual', {}).get('duration_min')
            if duration and context.session['duration_min'] > PROGRESSION_LIMIT * duration:
                return ['duration is more than 30% longer than the previous session']
        return []


class MediumRiskRule(SoftRule):
    def check(self, context):
        return ['the AI rated this session as medium risk'] if context.session['risk_level'] == 'medium' else []


class ProfilePreferences:
    """Encapsulates profile-derived choices; no mutation of the source dictionary."""
    def __init__(self, profile):
        self._profile = profile

    def available_days(self):
        try:
            days = int(self._profile.get('days_per_week', DEFAULT_DAYS_PER_WEEK))
        except (TypeError, ValueError):
            days = DEFAULT_DAYS_PER_WEEK
        return min(max(days, 1), 7)

    def available_minutes(self):
        try:
            minutes = int(self._profile.get('session_duration_min', MAX_DURATION_MIN))
        except (TypeError, ValueError):
            minutes = MAX_DURATION_MIN
        return min(max(minutes, MIN_DURATION_MIN), MAX_DURATION_MIN)

    def available_equipment(self):
        owned = {str(item).strip().lower() for item in (self._profile.get('equipment') or [])}
        owned.add('bodyweight')
        return owned

    def preferred_activities(self):
        chosen = self._profile.get('preferred_activities')
        return [str(item).strip().lower() for item in chosen] if chosen else None

    def missing_equipment_for(self, activity):
        needs = ACTIVITY_EQUIPMENT.get(activity, ())
        return [] if not needs or any(item in self.available_equipment() for item in needs) else list(needs)

    def usable_activities(self):
        chosen = self.preferred_activities()
        return None if chosen is None else [item for item in chosen if not self.missing_equipment_for(item)]


class WeeklyPlanner:
    """Calculates weekly goals independently from the evaluator."""
    def __init__(self, preferences):
        self._preferences = preferences  # aggregation: preferences may outlive planner

    def weekly_intensity_plan(self, days):
        return WEEKLY_PATTERNS[min(max(days, 1), 7)]

    def intensity_cap(self, profile, history):
        context = EvaluationContext({}, profile, history)
        if (context.has_health_concerns or profile.get('fitness_level') == 'beginner'
                or context.previous_hard or context.previous_planned_high):
            return 'moderate'
        return 'high'

    def weekly_target(self, profile, sessions_done, history):
        days = self._preferences.available_days()
        pattern = self.weekly_intensity_plan(days)
        week_complete = sessions_done >= days
        slot = 'low' if week_complete else pattern[sessions_done]
        cap = self.intensity_cap(profile, history)
        intensity = slot if INTENSITY_RANK[slot] <= INTENSITY_RANK[cap] else cap
        return {'intensity': intensity, 'session_number': sessions_done + 1,
                'sessions_per_week': days, 'week_complete': week_complete,
                'pattern': list(pattern)}


class SessionEvaluator:
    """Uses polymorphic rule objects; add rules without changing evaluation logic."""
    def __init__(self, rules=None):
        self._rules = list(rules) if rules is not None else [
            DurationRule(), HeartRateRule(), PaceRule(), RiskRule(), RecoveryRule(),
            EquipmentActivityRule(), WeeklyIntensityRule(), LowHeartRateRule(),
            ProgressionRule(), MediumRiskRule(),
        ]
        if not all(isinstance(rule, FitnessRule) for rule in self._rules):
            raise TypeError('all rules must implement FitnessRule')

    def collect_rejections(self, context):
        return [reason for rule in self._rules if rule.severity == 'reject' for reason in rule.check(context)]

    def collect_flags(self, context):
        return [reason for rule in self._rules if rule.severity == 'flag' for reason in rule.check(context)]

    def evaluate(self, context):
        rejections = self.collect_rejections(context)
        if rejections:
            return {'outcome': 'reject', 'reasons': rejections, 'score': 0}
        flags = self.collect_flags(context)
        return {'outcome': 'flag' if flags else 'accept', 'reasons': flags,
                'score': max(0, 100 - 20 * len(flags))}


class SessionLogValidator:
    def validate(self, log, profile):
        errors = []
        max_hr = 220 - profile['age']
        pace = log.get('avg_pace_min_per_km')
        if pace is not None and pace < MIN_PACE:
            errors.append(f'an average pace of {pace} min/km is not realistic')
        if log['avg_hr_bpm'] > max_hr + 10:
            errors.append(f"average heart rate {log['avg_hr_bpm']} bpm is far above your estimated max ({max_hr})")
        return errors


class LogicManager:
    """Facade for other managers. Owns evaluator and validator (composition)."""
    def __init__(self, rules=None):
        self._evaluator = SessionEvaluator(rules)
        self._log_validator = SessionLogValidator()

    def evaluate_session(self, session, profile, history, weekly_target=None):
        return self._evaluator.evaluate(EvaluationContext(session, profile, history, weekly_target))

    def collect_rejections(self, session, profile, history, weekly_target=None):
        return self._evaluator.collect_rejections(EvaluationContext(session, profile, history, weekly_target))

    def collect_flags(self, session, profile, history):
        return self._evaluator.collect_flags(EvaluationContext(session, profile, history))

    def weekly_target(self, profile, sessions_done, history):
        return WeeklyPlanner(ProfilePreferences(profile)).weekly_target(profile, sessions_done, history)

    def usable_activities(self, profile):
        return ProfilePreferences(profile).usable_activities()

    def missing_equipment_for(self, activity, profile):
        return ProfilePreferences(profile).missing_equipment_for(activity)

    def validate_session_log(self, log, profile):
        return self._log_validator.validate(log, profile)
