"""coach_service.py - AegisFit's application layer, shared by the Flet GUI and the console.

This is where the workflows that used to be procedural functions in main.py now live:
ask the AI for a session, run it past the safety rules, retry, save, log, report.

Nothing in this file prints, prompts or draws. Every method takes plain values and returns
plain results, so any user interface can drive it (Dependency Inversion: the UIs depend on
this service, the service depends only on the four managers' public methods).
"""
import datetime
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from ai_manager import AIManager
from data_manager import AppLogger, DataConfig, DataManager, LoadStatus
from logic_manager import LogicManager, ProgressReport
from settings_manager import AppSettings, SettingsManager, api_key_problem, clean_api_key

logger = logging.getLogger("coach_service")

APP_NAME = "AegisFit"
APP_VERSION = "2.1"
APP_TAGLINE = "The AI proposes. The code decides."

HISTORY_WINDOW = 5        # how many past sessions are sent to the AI
MAX_AI_ATTEMPTS = 3       # AI re-tries after the logic layer rejects its output

# Allowed ranges for profile fields; the GUI sliders and the validator both read these.
PROFILE_LIMITS = {
    "age": (12, 100),
    "height_cm": (100, 250),
    "weight_kg": (30, 250),
    "days_per_week": (1, 7),
    "session_duration_min": (10, 120),
}
FITNESS_LEVELS = ("beginner", "intermediate", "advanced")
SEX_OPTIONS = ("male", "female")
EQUIPMENT_OPTIONS = (
    "dumbbells", "barbell", "kettlebell", "resistance bands", "pull-up bar", "bench",
    "treadmill", "stationary bike", "rowing machine", "jump rope", "bicycle", "swimming pool",
)
ACTIVITY_OPTIONS = (
    "running", "cycling", "swimming", "walking", "strength training", "hiit", "rowing",
)

# Log limits (same as the console prompts).
LOG_LIMITS = {
    "duration_min": (1, 300),
    "avg_pace_min_per_km": (2, 30),
    "avg_hr_bpm": (30, 230),
    "rpe": (1, 10),
}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


# --------------------------------------------------------------------------- #
# Result objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SuggestionResult:
    """Outcome of one suggestion request. Exactly one of suggestion / error is set."""
    suggestion: Optional[dict] = None
    decision: Optional[dict] = None
    error: Optional[str] = None
    revisions: int = 0          # drafts the safety rules sent back before this one

    @property
    def ok(self):
        return self.suggestion is not None


@dataclass(frozen=True)
class WeeklyReportResult:
    """The weekly progress report plus the AI coach's note (or why there is none)."""
    report: ProgressReport
    coach_note: Optional[dict] = None
    note_error: Optional[str] = None


# --------------------------------------------------------------------------- #
# Calendar events: one interface, two kinds of session
# --------------------------------------------------------------------------- #
class CalendarEvent(ABC):
    """A session shown on a calendar day. The GUI renders any event through this
    interface without checking which subclass it has (polymorphism)."""

    def __init__(self, day, session):
        self._day = day
        self._session = session or {}

    @property
    def day(self):
        return self._day

    @property
    def plan(self):
        return self._session.get("planned") or self._session

    @property
    def title(self):
        return str(self.plan.get("exercise_type") or self.plan.get("activity") or "Session").strip()

    @property
    def activity(self):
        return str(self.plan.get("activity") or "").strip().lower()

    @property
    @abstractmethod
    def status(self):
        """'done', 'pain' or 'planned' - decides the colour of the calendar marker."""

    @abstractmethod
    def summary(self):
        """One short line for a list row."""

    @abstractmethod
    def details(self):
        """List of (label, value) pairs for the detail sheet."""


class CompletedSession(CalendarEvent):
    """A session that was logged: shows what was actually done."""

    @property
    def actual(self):
        return self._session.get("actual") or {}

    @property
    def status(self):
        return "pain" if self.actual.get("pain_reported") else "done"

    def summary(self):
        parts = ["{} min".format(self.actual.get("duration_min", "?"))]
        if self.actual.get("avg_hr_bpm"):
            parts.append("{} bpm".format(self.actual["avg_hr_bpm"]))
        if self.actual.get("rpe") is not None:
            parts.append("RPE {}".format(self.actual["rpe"]))
        if self.actual.get("pain_reported"):
            parts.append("pain")
        return " · ".join(parts)

    def details(self):
        pace = self.actual.get("avg_pace_min_per_km")
        return [
            ("Activity", self.activity or "-"),
            ("Planned", "{} min, {} intensity".format(self.plan.get("duration_min", "?"),
                                                    self.plan.get("intensity", "?"))),
            ("Done", "{} min".format(self.actual.get("duration_min", "?"))),
            ("Pace", "-" if pace is None else "{} min/km".format(pace)),
            ("Heart rate", "{} bpm".format(self.actual.get("avg_hr_bpm", "-"))),
            ("Effort", "RPE {}".format(self.actual.get("rpe", "-"))),
            ("Pain", "yes" if self.actual.get("pain_reported") else "no"),
            ("Comments", self.actual.get("comments") or "-"),
        ]


class PlannedSession(CalendarEvent):
    """The accepted session that has not been logged yet: shows the plan."""

    @property
    def status(self):
        return "planned"

    def summary(self):
        return "{} min · {} intensity".format(self.plan.get("duration_min", "?"), self.plan.get("intensity", "?"))

    def details(self):
        pace = self.plan.get("target_pace_min_per_km")
        return [
            ("Activity", self.activity or "-"),
            ("Duration", "{} min".format(self.plan.get("duration_min", "?"))),
            ("Intensity", self.plan.get("intensity", "-")),
            ("Target HR", "{} bpm".format(self.plan.get("target_hr_bpm", "-"))),
            ("Target pace", "-" if pace is None else "{} min/km".format(pace)),
            ("Warm-up", self.plan.get("warmup", "-")),
            ("Main set", self.plan.get("main_set", "-")),
            ("Cool-down", self.plan.get("cooldown", "-")),
        ]


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
class CoachService:
    """Facade over the four managers for any user interface.

    Association: the managers are handed in and only used, so tests can pass stubs
    (for example an AIManager with a fake LLM client and a DataManager on InMemoryStorage).
    Settings are optional: without a SettingsManager the defaults are used and nothing is saved.
    """

    SCHEDULE_KEY = "scheduled_for"   # extra key stored on the pending session

    def __init__(self, data, ai, logic, clock=datetime.date.today, settings=None):
        self._data = data
        self._ai = ai
        self._logic = logic
        self._clock = clock
        self._settings = settings or SettingsManager.in_memory()
        self._apply_ai_settings()

    @classmethod
    def create(cls, config=None):
        """Build the real app: JSON storage from the environment, saved settings, Claude, default rules."""
        config = config or DataConfig.from_env()
        AppLogger(config.log_file).configure()
        settings = SettingsManager.from_env(config)
        settings.load()          # small file, read before the GUI draws so the theme is right
        # One clock for the whole app, in the user's time zone (the server may be on UTC).
        return cls(DataManager.from_config(config, clock=settings.today), AIManager(), LogicManager(),
                   clock=settings.today, settings=settings)

    # ---- settings ----
    @property
    def settings(self):
        """The current AppSettings (immutable; change them with save_settings)."""
        return self._settings.settings

    @property
    def settings_status(self):
        return self._settings.status

    @property
    def settings_location(self):
        return self._settings.location

    @property
    def timezone_name(self):
        """Zone used for 'today' ('' means the computer's own clock)."""
        return self._settings.timezone_name

    @property
    def data_location(self):
        return self._data.location

    @property
    def ai_model(self):
        return getattr(self._ai, "model", "-")

    def api_key_source(self):
        """'settings', 'environment' or None (no key, so the coach cannot plan sessions)."""
        return self._settings.api_key_source()

    def environment_key_problem(self):
        """What is wrong with ANTHROPIC_API_KEY, if it is set but malformed."""
        return self._settings.environment_key_problem()

    def save_settings(self, settings):
        """Validate and save new settings, then use the new key and model straight away.
        Returns a list of problems (empty on success)."""
        if not isinstance(settings, AppSettings):
            return ["settings must be an AppSettings object"]
        errors = self._settings.save(settings)
        if not errors:
            self._apply_ai_settings()
        return errors

    def test_ai_connection(self, api_key=None, model=None):
        """(ok, message) for a key typed but not saved yet, or the key in use.
        Blocking network call, so GUIs run it off the UI thread."""
        key = clean_api_key(api_key) if api_key else None
        problem = api_key_problem(key)
        if problem:
            return False, problem
        return self._ai.verify_connection(api_key=key, model=model)

    def _apply_ai_settings(self):
        configure = getattr(self._ai, "configure", None)   # tests may build the service without an AI layer
        if configure:
            current = self._settings.settings
            configure(api_key=current.api_key or None, model=current.model)

    # ---- start-up and state ----
    def load(self):
        """Load saved data. Returns a LoadStatus (ok / missing / corrupt)."""
        return self._data.load()

    @property
    def today(self):
        return self._clock()

    @property
    def profile(self):
        return self._data.profile

    @property
    def has_profile(self):
        return self._data.profile is not None

    @property
    def pending_session(self):
        return self._data.pending_session

    def pending_date(self):
        """The day the pending session is scheduled for (today if it has none)."""
        pending = self._data.pending_session
        if not pending:
            return None
        try:
            return datetime.date.fromisoformat(pending.get(self.SCHEDULE_KEY))
        except (TypeError, ValueError):
            return self.today

    # ---- profile ----
    def validate_profile(self, profile):
        """Return a list of problems; an empty list means the profile can be saved."""
        errors = []
        for name, (low, high) in PROFILE_LIMITS.items():
            value = profile.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
                errors.append("{} must be between {} and {}".format(name.replace("_", " "), low, high))
        if profile.get("sex") not in SEX_OPTIONS:
            errors.append("choose a sex")
        if profile.get("fitness_level") not in FITNESS_LEVELS:
            errors.append("choose a fitness level")
        if not str(profile.get("fitness_goal") or "").strip():
            errors.append("enter a fitness goal")
        if not profile.get("preferred_activities"):
            errors.append("choose at least one activity")
        elif not self._logic.usable_activities(profile):
            errors.append("none of your chosen activities can be done with your equipment")
        return errors

    def equipment_notes(self, profile):
        """Activities that will be skipped until the user adds the needed equipment."""
        notes = []
        for activity in profile.get("preferred_activities") or []:
            needed = self._logic.missing_equipment_for(activity, profile)
            if needed:
                notes.append("{} needs {}; it is skipped until you add it.".format(activity, " or ".join(needed)))
        return notes

    def save_profile(self, profile):
        """Validate and save. Returns a list of errors (empty on success)."""
        profile = dict(profile)
        profile["health_concerns"] = str(profile.get("health_concerns") or "").strip() or "none"
        profile["fitness_goal"] = str(profile.get("fitness_goal") or "").strip()
        errors = self.validate_profile(profile)
        if errors:
            return errors
        if not self._data.save_profile(profile):
            return ["could not save your profile to disk"]
        return []

    # ---- planning ----
    def weekly_target(self):
        """Where the next session sits in the user's week."""
        history = self._data.get_recent_sessions(HISTORY_WINDOW)
        done = self._data.sessions_this_week()
        return self._logic.weekly_target(self._data.profile, done, history)

    def suggest_session(self, user_feedback=None, weekly_target=None,
                        on_revision: Optional[Callable[[list], None]] = None):
        """AI -> safety rules loop. Blocking (calls the API), so GUIs run it off the UI thread.

        on_revision, if given, is called with the rejection reasons each time a draft is sent back.
        """
        if not self.has_profile:
            return SuggestionResult(error="set up your profile first")
        profile = self._data.profile
        history = self._data.get_recent_sessions(HISTORY_WINDOW)
        weekly_target = weekly_target or self.weekly_target()
        rule_feedback = None
        for attempt in range(MAX_AI_ATTEMPTS):
            result = self._ai.request_session(profile, history, user_feedback, rule_feedback, weekly_target)
            if not result.ok:
                return SuggestionResult(error="coach unavailable: {}".format(result.error), revisions=attempt)
            decision = self._logic.evaluate_session(result.data, profile, history, weekly_target)
            if decision["outcome"] != "reject":
                return SuggestionResult(result.data, decision, revisions=attempt)
            rule_feedback = decision["reasons"]
            logger.info("Draft %d rejected by safety rules: %s", attempt + 1, rule_feedback)
            if on_revision:
                on_revision(rule_feedback)
        return SuggestionResult(error="the coach could not produce a safe session", revisions=MAX_AI_ATTEMPTS)

    def accept_session(self, suggestion, scheduled_for=None):
        """Save an accepted suggestion as the planned session on the given day (default today)."""
        day = scheduled_for or self.today
        return self._data.set_pending_session(dict(suggestion, **{self.SCHEDULE_KEY: day.isoformat()}))

    def reschedule_pending(self, day):
        pending = self._data.pending_session
        if not pending:
            return False
        pending[self.SCHEDULE_KEY] = day.isoformat()
        return self._data.set_pending_session(pending)

    def discard_pending(self):
        return self._data.set_pending_session(None)

    # ---- logging ----
    def validate_log(self, log):
        errors = []
        for name, (low, high) in LOG_LIMITS.items():
            value = log.get(name)
            if value is None and name == "avg_pace_min_per_km":
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
                errors.append("{} must be between {} and {}".format(name.replace("_", " "), low, high))
        if errors:
            return errors
        return self._logic.validate_session_log(log, self._data.profile)

    def log_session(self, log):
        """Validate and record the pending session as done. Returns a list of errors."""
        if not self._data.pending_session:
            return ["there is no planned session to log"]
        errors = self.validate_log(log)
        if errors:
            return errors
        if not self._data.complete_pending_session(log):
            return ["could not save the session to disk"]
        return []

    # ---- history and calendar ----
    def history(self, pain_only=False, min_rpe=None):
        return self._data.filter_sessions(min_rpe=min_rpe, pain_only=pain_only)

    def calendar_events(self, year, month, pain_only=False, min_rpe=None):
        """Events for one month, as {date: [CalendarEvent, ...]}."""
        events = {}
        for record in self.history(pain_only=pain_only, min_rpe=min_rpe):
            try:
                day = datetime.date.fromisoformat(record.get("date"))
            except (TypeError, ValueError):
                continue
            if (day.year, day.month) == (year, month):
                events.setdefault(day, []).append(CompletedSession(day, record))
        planned_day = self.pending_date()
        if planned_day and (planned_day.year, planned_day.month) == (year, month) and not (pain_only or min_rpe):
            events.setdefault(planned_day, []).append(PlannedSession(planned_day, self._data.pending_session))
        return events

    # ---- weekly progress report ----
    def training_load(self, weeks=6):
        """[(week_start, minutes, sessions), ...] oldest first, for the training-load chart."""
        load = []
        for weeks_ago in range(weeks - 1, -1, -1):
            sessions = self._data.sessions_in_week(weeks_ago)
            minutes = sum(_number((s.get("actual") or {}).get("duration_min")) for s in sessions)
            load.append((self._data.week_start(weeks_ago), minutes, len(sessions)))
        return load

    def weekly_report(self, with_coach_note=True):
        """This week vs last week. The AI note is only requested when there is something to discuss."""
        report = self._logic.weekly_progress_report(
            self._data.profile, self._data.sessions_in_week(0), self._data.sessions_in_week(1),
            self._data.week_start(0))
        if not with_coach_note or not report.has_sessions:
            return WeeklyReportResult(report)
        result = self._ai.request_weekly_note(self._data.profile, report.to_dict())
        if result.ok:
            return WeeklyReportResult(report, coach_note=result.data)
        return WeeklyReportResult(report, note_error=result.error)

    # ---- reset ----
    def reset(self):
        return self._data.reset()


__all__ = [
    "APP_NAME", "APP_VERSION", "APP_TAGLINE", "CoachService", "SuggestionResult", "WeeklyReportResult", "CalendarEvent", "CompletedSession",
    "PlannedSession", "LoadStatus", "PROFILE_LIMITS", "FITNESS_LEVELS", "SEX_OPTIONS",
    "EQUIPMENT_OPTIONS", "ACTIVITY_OPTIONS", "LOG_LIMITS",
]
