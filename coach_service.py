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

from activity_import import IMPORTERS
from ai_manager import AIManager
from data_manager import AppLogger, DataConfig, DataManager, LoadStatus
from logic_manager import LogicManager, ProgressReport, pace_text
from settings_manager import AppSettings, SettingsManager, api_key_problem, clean_api_key

logger = logging.getLogger("coach_service")

APP_NAME = "AegisFit"
APP_VERSION = "2.2"
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
class ImportSummary:
    """Outcome of importing an exported history file."""
    added: int = 0
    already_imported: int = 0
    skipped: tuple = ()             # (row number, reason) for rows that could not be read
    error: Optional[str] = None
    first_day: Optional[datetime.date] = None
    last_day: Optional[datetime.date] = None

    @property
    def ok(self):
        return self.error is None

    def message(self):
        """One line for the user."""
        if self.error:
            return "Import failed: " + self.error
        parts = []
        if self.added:
            span = ""
            if self.first_day and self.last_day:
                span = " ({} to {})".format(self.first_day.strftime("%d %b %Y"), self.last_day.strftime("%d %b %Y"))
            parts.append("Imported {} {}{}".format(self.added, "activity" if self.added == 1 else "activities", span))
        else:
            parts.append("No new activities")
        if self.already_imported:
            parts.append("{} already imported".format(self.already_imported))
        if self.skipped:
            parts.append("{} unreadable {} skipped".format(len(self.skipped), "row" if len(self.skipped) == 1 else "rows"))
        return ", ".join(parts) + "."


@dataclass(frozen=True)
class FeedbackResult:
    """The coach's review of one session. review (the app's own comparison) is set whenever the
    session was found, even if the AI could not be reached."""
    feedback: Optional[dict] = None
    review: Optional[object] = None
    error: Optional[str] = None
    saved: bool = True

    @property
    def ok(self):
        return self.feedback is not None


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
    def record_id(self):
        """Stored id (logged sessions: a number, imported activities: 'strava-...'), None if unsaved."""
        return self._session.get("id")

    @property
    def feedback(self):
        """The coach's saved review of this session, if one was asked for."""
        feedback = self._session.get("coach_feedback")
        return feedback if isinstance(feedback, dict) else None

    @property
    def can_review(self):
        """Whether the coach can give feedback (only on sessions that have been done)."""
        return False

    @property
    def minutes(self):
        """Minutes actually trained, or None for a session not done yet."""
        return None

    @property
    def effort(self):
        """How hard it felt (RPE 1-10), if known."""
        return None

    def comparison_inputs(self):
        """(planned, actual) for the logic layer's session review."""
        return None, {}

    def as_payload(self):
        """The session as the coach should see it when writing feedback."""
        return {"date": self._day.isoformat()}

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

    _PLAN_FIELDS = ("exercise_type", "activity", "duration_min", "intensity", "target_hr_bpm",
                    "target_pace_min_per_km", "main_set", "rationale")

    @property
    def actual(self):
        return self._session.get("actual") or {}

    @property
    def can_review(self):
        return self.record_id is not None

    @property
    def minutes(self):
        return _number(self.actual.get("duration_min"))

    @property
    def effort(self):
        value = self.actual.get("rpe")
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def comparison_inputs(self):
        return (self._session.get("planned") or None), self.actual

    def as_payload(self):
        plan = self._session.get("planned") or {}
        return {"date": self._day.isoformat(),
                "planned": {field: plan.get(field) for field in self._PLAN_FIELDS if field in plan},
                "actual": dict(self.actual)}

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
            ("Pace", "-" if pace is None else pace_text(pace)),
            ("Heart rate", "{} bpm".format(self.actual.get("avg_hr_bpm", "-"))),
            ("Effort", "RPE {}".format(self.actual.get("rpe", "-"))),
            ("Pain", "yes" if self.actual.get("pain_reported") else "no"),
            ("Comments", self.actual.get("comments") or "-"),
        ]


class ImportedSession(CalendarEvent):
    """Past training imported from Strava: shows its key stats. It has no plan, so the coach
    reviews it on its own rather than against one."""

    _STAT_FIELDS = ("name", "activity", "sport_type", "start_time", "duration_min", "elapsed_min", "distance_km",
                    "avg_pace_min_per_km", "avg_speed_kmh", "avg_hr_bpm", "max_hr_bpm", "elevation_gain_m",
                    "calories", "relative_effort", "perceived_exertion", "description")

    @property
    def title(self):
        return str(self._session.get("name") or self._session.get("sport_type") or "Activity").strip()

    @property
    def source(self):
        return str(self._session.get("source") or "import")

    @property
    def status(self):
        return "imported"

    @property
    def can_review(self):
        return True

    @property
    def minutes(self):
        return _number(self._session.get("duration_min"))

    @property
    def effort(self):
        value = self._session.get("perceived_exertion")
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def _get(self, key):
        return self._session.get(key)

    def summary(self):
        parts = []
        if self._get("distance_km"):
            parts.append("{:g} km".format(self._get("distance_km")))
        parts.append("{:g} min".format(self.minutes or 0))
        if self._get("avg_pace_min_per_km"):
            parts.append(pace_text(self._get("avg_pace_min_per_km")))
        elif self._get("avg_speed_kmh"):
            parts.append("{:g} km/h".format(self._get("avg_speed_kmh")))
        if self._get("avg_hr_bpm"):
            parts.append("{} bpm".format(self._get("avg_hr_bpm")))
        return " · ".join(parts)

    def details(self):
        rows = [("Source", self.source.capitalize()), ("Type", self._get("sport_type") or self.activity or "-")]
        optional = (
            ("Started", "start_time", "{}"),
            ("Distance", "distance_km", "{:g} km"),
            ("Moving time", "duration_min", "{:g} min"),
            ("Elapsed", "elapsed_min", "{:g} min"),
            ("Pace", "avg_pace_min_per_km", None),
            ("Speed", "avg_speed_kmh", "{:g} km/h"),
            ("Avg heart rate", "avg_hr_bpm", "{} bpm"),
            ("Max heart rate", "max_hr_bpm", "{} bpm"),
            ("Elevation gain", "elevation_gain_m", "{} m"),
            ("Calories", "calories", "{} kcal"),
            ("Relative effort", "relative_effort", "{}"),
            ("Perceived effort", "perceived_exertion", "RPE {}"),
            ("Notes", "description", "{}"),
        )
        for name, key, pattern in optional:
            value = self._get(key)
            if value in (None, ""):
                continue
            rows.append((name, pace_text(value) if pattern is None else pattern.format(value)))
        return rows

    def comparison_inputs(self):
        return None, {"duration_min": self._get("duration_min"), "avg_hr_bpm": self._get("avg_hr_bpm"),
                      "avg_pace_min_per_km": self._get("avg_pace_min_per_km"), "rpe": self.effort}

    def as_payload(self):
        stats = {key: self._session[key] for key in self._STAT_FIELDS if key in self._session}
        return dict(stats, date=self._day.isoformat(), source=self.source)


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

    def __init__(self, data, ai, logic, clock=datetime.date.today, settings=None, importers=None):
        self._data = data
        self._ai = ai
        self._logic = logic
        self._clock = clock
        self._settings = settings or SettingsManager.in_memory()
        self._importers = dict(importers or IMPORTERS)   # source name -> ActivityImporter class
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
        """Events for one month, as {date: [CalendarEvent, ...]}: logged, imported and planned."""
        events = {}
        for record in self.history(pain_only=pain_only, min_rpe=min_rpe):
            try:
                day = datetime.date.fromisoformat(record.get("date"))
            except (TypeError, ValueError):
                continue
            if (day.year, day.month) == (year, month):
                events.setdefault(day, []).append(CompletedSession(day, record))
        if not pain_only:          # exports do not record pain, so imported activities never match that filter
            for activity in self._data.imported_in_month(year, month):
                event = ImportedSession(datetime.date.fromisoformat(activity["date"]), activity)
                if min_rpe is None or (event.effort is not None and event.effort >= min_rpe):
                    events.setdefault(event.day, []).append(event)
        planned_day = self.pending_date()
        if planned_day and (planned_day.year, planned_day.month) == (year, month) and not (pain_only or min_rpe):
            events.setdefault(planned_day, []).append(PlannedSession(planned_day, self._data.pending_session))
        return events

    # ---- imported history (Strava) ----
    @property
    def import_sources(self):
        return tuple(self._importers)

    def import_history(self, data, source="strava"):
        """Import an exported history file (raw bytes). Nothing goes to the AI: imported activities
        are shown on the calendar as they are. Returns an ImportSummary."""
        importer_class = self._importers.get(source)
        if importer_class is None:
            return ImportSummary(error="importing from {} is not supported".format(source))
        result = importer_class().parse(data, self._settings.tzinfo())
        if not result.ok:
            return ImportSummary(skipped=result.skipped, error=result.error)
        added, already, saved = self._data.import_activities(result.activities)
        if not saved:
            return ImportSummary(skipped=result.skipped, error="could not save the imported activities to disk")
        days = sorted(activity["date"] for activity in result.activities)
        first, last = (datetime.date.fromisoformat(days[0]), datetime.date.fromisoformat(days[-1])) if days else (None, None)
        logger.info("Imported %d %s activities (%d already stored, %d rows skipped)",
                    added, source, already, len(result.skipped))
        return ImportSummary(added, already, result.skipped, first_day=first, last_day=last)

    @property
    def imported_count(self):
        return self._data.imported_count

    def imported_overview(self):
        """(count, first day, last day) of the imported history, for the Profile tab."""
        activities = self._data.imported_activities()
        if not activities:
            return 0, None, None
        return (len(activities), datetime.date.fromisoformat(activities[0]["date"]),
                datetime.date.fromisoformat(activities[-1]["date"]))

    def clear_imported(self):
        return self._data.clear_imported()

    # ---- coach feedback on one session ----
    def review_event(self, event):
        """The app's own plan-vs-actual comparison (a logic_manager.SessionReview). No AI, instant."""
        planned, actual = event.comparison_inputs()
        return self._logic.review_session(planned, actual, self._data.profile or {})

    def event_for(self, record_id):
        """The stored session or imported activity with this id, as a CalendarEvent (or None)."""
        kind, record = self._data.find_record(record_id)
        if record is None:
            return None
        try:
            day = datetime.date.fromisoformat(record.get("date"))
        except (TypeError, ValueError):
            return None
        return (ImportedSession if kind == "imported" else CompletedSession)(day, record)

    def session_feedback(self, record_id):
        """Ask the coach to review one finished session, and save the review with it.
        Blocking (calls the API), so GUIs run it off the UI thread. Returns a FeedbackResult."""
        if not self.has_profile:
            return FeedbackResult(error="set up your profile first, so the coach knows your goal")
        event = self.event_for(record_id)
        if event is None or not event.can_review:
            return FeedbackResult(error="that session could not be found")
        review = self.review_event(event)
        result = self._ai.request_session_feedback(self._data.profile, event.as_payload(), review.to_dict())
        if not result.ok:
            return FeedbackResult(review=review, error="coach unavailable: {}".format(result.error))
        feedback = {
            "summary": result.data["summary"].strip(),
            "went_well": [point.strip() for point in result.data["went_well"] if point.strip()][:3],
            "to_improve": [point.strip() for point in result.data["to_improve"] if point.strip()][:3],
            "next_time": result.data["next_time"].strip(),
            "adherence": review.adherence,       # the code's verdict, not the AI's
            "generated_on": self.today.isoformat(),
            "model": self.ai_model,
        }
        saved = self._data.attach_feedback(record_id, feedback)
        return FeedbackResult(feedback, review, saved=saved)

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
    "PlannedSession", "ImportedSession", "ImportSummary", "FeedbackResult", "LoadStatus", "PROFILE_LIMITS", "FITNESS_LEVELS", "SEX_OPTIONS",
    "EQUIPMENT_OPTIONS", "ACTIVITY_OPTIONS", "LOG_LIMITS",
]
