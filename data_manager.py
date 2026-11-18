import copy
import datetime
import glob
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("data_manager")


# --------------------------------------------------------------------------- #
# Configuration and logging
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DataConfig:
    
    data_file: str = "data/fitness_data.json"
    log_file: str = "data/app.log"

    @classmethod
    def from_env(cls):
        return cls(
            data_file=os.environ.get("FITNESS_DATA_FILE", cls.data_file),
            log_file=os.environ.get("FITNESS_LOG_FILE", cls.log_file),
        )
class AppLogger:

    def __init__(self, log_file):
        self._log_file = log_file

    def configure(self):
        try:
            os.makedirs(os.path.dirname(self._log_file) or ".", exist_ok=True)
            logging.basicConfig(
                filename=self._log_file,
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
        except OSError:
            # A log file is nice to have, never worth crashing for: run without logging.
            logging.disable(logging.CRITICAL)
class LoadStatus(str, Enum):

    OK = "ok"
    MISSING = "missing"
    CORRUPT = "corrupt"

class Storage(ABC):
    """Interface (contract) for anything that can persist the store payload.

    DataManager depends only on this abstraction (Dependency Inversion / OCP):
    a database backend can be added later without touching DataManager.
    Every implementation must be failure-tolerant: methods never raise.
    """

    @abstractmethod
    def load(self):
        """Return (payload, LoadStatus). payload is None unless status is OK."""

    @abstractmethod
    def save(self, payload):
        """Persist payload. Return True on success."""

    @abstractmethod
    def quarantine(self):
        """Move unusable stored data aside so it is never silently overwritten."""

    @abstractmethod
    def erase(self):
        """Delete everything this storage holds. Return True only if all of it was removed."""


class JsonFileStorage(Storage):

    def __init__(self, path):
        self._path = path

    @property
    def path(self):
        return self._path

    def load(self):
        if not os.path.exists(self._path):
            logger.info("No data file at %s; starting fresh", self._path)
            return None, LoadStatus.MISSING
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.error("Failed to read %s: %s", self._path, exc)
            self.quarantine()
            return None, LoadStatus.CORRUPT
        return payload, LoadStatus.OK

    def save(self, payload):
        tmp_path = self._path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
            return True
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Failed to save %s: %s", self._path, exc)
            return False

    def quarantine(self):
        backup = "{}.corrupt-{}".format(self._path, int(time.time()))
        try:
            os.replace(self._path, backup)
            logger.error("Corrupt data file moved to %s", backup)
        except OSError as exc:
            logger.error("Could not back up corrupt file %s: %s", self._path, exc)

    def erase(self):

        targets = [self._path, self._path + ".tmp"]
        targets += glob.glob(glob.escape(self._path) + ".corrupt-*")
        all_removed = True
        for target in targets:
            try:
                os.remove(target)
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.error("Could not delete %s: %s", target, exc)
                all_removed = False
        return all_removed
        
class InMemoryStorage(Storage):

    def __init__(self, payload=None, succeed=True):
        self._payload = copy.deepcopy(payload)
        self._succeed = succeed          # set False to simulate a failing disk

    def load(self):
        if self._payload is None:
            return None, LoadStatus.MISSING
        return copy.deepcopy(self._payload), LoadStatus.OK

    def save(self, payload):
        if not self._succeed:
            return False
        self._payload = copy.deepcopy(payload)
        return True

    def quarantine(self):
        self._payload = None

    def erase(self):
        if not self._succeed:
            return False
        self._payload = None
        return True

class Record(ABC):
    """Abstract base for anything persisted. Shares equality and repr; subclasses
    define how they turn into / are built from a plain dict."""

    @abstractmethod
    def to_dict(self):
        """Return a plain, JSON-serialisable copy of this record."""

    @classmethod
    @abstractmethod
    def from_dict(cls, data):
        """Build a record from a dict. Raise ValueError if the data is unusable."""

    def __eq__(self, other):
        return type(self) is type(other) and self.to_dict() == other.to_dict()

    __hash__ = None

    def __repr__(self):
        return "{}({})".format(type(self).__name__, self.to_dict())

class Profile(Record):

    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def to_dict(self):
        return copy.deepcopy(self._data)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("profile must be a dict")
        return cls(data)

class SessionRecord(Record):

    def __init__(self, record_id, date, planned, actual, feedback=None):
        self._id = record_id
        self._date = date
        self._planned = copy.deepcopy(planned)
        self._actual = copy.deepcopy(actual)
        self._feedback = copy.deepcopy(feedback)    # the coach's review, once asked for

    @property
    def id(self):
        return self._id

    def set_feedback(self, feedback):
        self._feedback = copy.deepcopy(feedback)

    @property
    def rpe(self):
        value = self._actual.get("rpe", 0) if isinstance(self._actual, dict) else 0
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    @property
    def pain_reported(self):
        return isinstance(self._actual, dict) and bool(self._actual.get("pain_reported", False))

    @property
    def logged_on(self):
        try:
            return datetime.date.fromisoformat(self._date)
        except (TypeError, ValueError):
            return None

    def to_dict(self):
        data = {
            "id": self._id,
            "date": self._date,
            "planned": copy.deepcopy(self._planned),
            "actual": copy.deepcopy(self._actual),
        }
        if self._feedback is not None:
            data["coach_feedback"] = copy.deepcopy(self._feedback)
        return data

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("session record must be a dict")
        feedback = data.get("coach_feedback")
        return cls(data.get("id"), data.get("date"), data.get("planned"), data.get("actual", {}),
                   feedback if isinstance(feedback, dict) else None)


class ImportedActivity(Record):
    """An activity imported from another app (see activity_import.py): history to show, not a plan.

    Stored as a flat dict of stats. Only id, source and date are required; every stat is optional
    because exports leave many of them blank."""

    def __init__(self, data):
        self._data = copy.deepcopy(data)

    @property
    def id(self):
        return self._data["id"]

    @property
    def day(self):
        return datetime.date.fromisoformat(self._data["date"])

    @property
    def perceived_exertion(self):
        value = self._data.get("perceived_exertion")
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def set_feedback(self, feedback):
        if feedback is None:
            self._data.pop("coach_feedback", None)
        else:
            self._data["coach_feedback"] = copy.deepcopy(feedback)

    def to_dict(self):
        return copy.deepcopy(self._data)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("imported activity must be a dict")
        if not isinstance(data.get("id"), str) or not data["id"].strip():
            raise ValueError("imported activity needs an id")
        if not isinstance(data.get("source"), str):
            raise ValueError("imported activity needs a source")
        try:
            datetime.date.fromisoformat(data.get("date"))
        except (TypeError, ValueError):
            raise ValueError("imported activity has no valid date") from None
        return cls(data)


class ActivityLog:
    """Imported activities, oldest first, at most one per id (so importing the same file twice adds nothing)."""

    def __init__(self, activities=None):
        self._items = {}
        for activity in activities or []:
            self._items.setdefault(activity.id, activity)

    def __len__(self):
        return len(self._items)

    def merge(self, activities):
        """Add the activities that are not stored yet. Returns (added, already_stored).
        An activity already stored keeps its saved coach feedback."""
        added = already = 0
        for activity in activities:
            if activity.id in self._items:
                already += 1
            else:
                self._items[activity.id] = activity
                added += 1
        return added, already

    def find(self, activity_id):
        return self._items.get(activity_id)

    def all(self):
        return sorted(self._items.values(), key=lambda a: (a.day, a.to_dict().get("start_time", "")))

    def in_month(self, year, month):
        return [a for a in self.all() if (a.day.year, a.day.month) == (year, month)]

    def copy(self):
        return ActivityLog(ImportedActivity(a.to_dict()) for a in self._items.values())

    def to_list(self):
        return [activity.to_dict() for activity in self.all()]

    @classmethod
    def from_list(cls, items):
        """Rebuild from storage. One damaged entry is skipped (and logged) rather than throwing
        away the rest of the user's data: imported history can always be imported again."""
        if not isinstance(items, list):
            raise ValueError("imported_activities must be a list")
        activities = []
        for item in items:
            try:
                activities.append(ImportedActivity.from_dict(item))
            except ValueError as exc:
                logger.error("Skipping a damaged imported activity: %s", exc)
        return cls(activities)

class SessionFilter(ABC):

    @abstractmethod
    def matches(self, record):
        pass
class MinRpeFilter(SessionFilter):
    def __init__(self, min_rpe):
        self._min_rpe = min_rpe

    def matches(self, record):
        return record.rpe >= self._min_rpe


class PainReportedFilter(SessionFilter):
    def matches(self, record):
        return record.pain_reported


class WeekFilter(SessionFilter):
    """Sessions logged in the same Monday-Sunday week as `day` (weekly progress report)."""

    def __init__(self, day):
        self._week = tuple(day.isocalendar())[:2]

    def matches(self, record):
        logged = record.logged_on
        return logged is not None and tuple(logged.isocalendar())[:2] == self._week

class SessionHistory:

    def __init__(self, records=None):
        self._records = list(records or [])

    def __len__(self):
        return len(self._records)

    def add(self, planned, actual, date):
        record = SessionRecord(len(self._records) + 1, date, planned, actual)
        self._records.append(record)
        return record

    def recent(self, count):
        if count <= 0:
            return []
        return self._records[-count:]

    def count_in_week_of(self, today):
        
        this_week = tuple(today.isocalendar())[:2]
        return sum(
            1 for record in self._records
            if record.logged_on is not None
            and tuple(record.logged_on.isocalendar())[:2] == this_week
        )

    def query(self, *filters):
        return [r for r in self._records if all(f.matches(r) for f in filters)]

    def find(self, record_id):
        return next((record for record in self._records if record.id == record_id), None)

    def to_list(self):
        return [record.to_dict() for record in self._records]

    @classmethod
    def from_list(cls, items):
        if not isinstance(items, list):
            raise ValueError("sessions must be a list")
        return cls(SessionRecord.from_dict(item) for item in items)

class DataManager:
  
    def __init__(self, storage, clock=datetime.date.today):
        self._storage = storage          # aggregation: injected, can outlive this manager
        self._clock = clock              # injectable so tests control "today"
        self._profile = None
        self._pending_session = None
        self._history = SessionHistory()  # composition: created and replaced here
        self._imports = ActivityLog()     # composition: imported history (Strava), same lifecycle

    @classmethod
    def from_config(cls, config=None, clock=None):
        config = config or DataConfig.from_env()
        return cls(JsonFileStorage(config.data_file), clock=clock or datetime.date.today)

    @property
    def location(self):
        """Where the data is stored (a file path, or a description for in-memory storage)."""
        return getattr(self._storage, "path", "in memory (not saved to disk)")

    # ---- read-only views (copies, so callers cannot corrupt internal state) ----
    @property
    def profile(self):
        return self._profile.to_dict() if self._profile else None

    @property
    def pending_session(self):
        return copy.deepcopy(self._pending_session)

    @property
    def sessions(self):
        return self._history.to_list()

    # ---- load / save ----
    def load(self):
        payload, status = self._storage.load()
        if status is LoadStatus.OK:
            try:
                self._apply(payload)
            except ValueError as exc:
                logger.error("Stored data has an invalid structure: %s", exc)
                self._storage.quarantine()
                status = LoadStatus.CORRUPT
        if status is not LoadStatus.OK:
            self._clear_state()
        return status

    def _apply(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("top level must be a dict")
        raw_profile = payload.get("profile")
        pending = payload.get("pending_session")
        if pending is not None and not isinstance(pending, dict):
            raise ValueError("pending_session must be a dict or null")
        profile = Profile.from_dict(raw_profile) if raw_profile is not None else None
        history = SessionHistory.from_list(payload.get("sessions"))
        imports = ActivityLog.from_list(payload.get("imported_activities") or [])   # absent in older files
        self._profile, self._pending_session, self._history = profile, copy.deepcopy(pending), history
        self._imports = imports

    def _to_payload(self):
        return {
            "profile": self._profile.to_dict() if self._profile else None,
            "pending_session": copy.deepcopy(self._pending_session),
            "sessions": self._history.to_list(),
            "imported_activities": self._imports.to_list(),
        }

    def _clear_state(self):
        self._profile = None
        self._pending_session = None
        self._history = SessionHistory()
        self._imports = ActivityLog()

    def _save(self):
        return self._storage.save(self._to_payload())

    # ---- commands ----
    def save_profile(self, profile):
        self._profile = Profile.from_dict(profile)
        return self._save()

    def set_pending_session(self, suggestion):
        self._pending_session = copy.deepcopy(suggestion)
        return self._save()

    def complete_pending_session(self, actual):
        self._history.add(self._pending_session, actual, self._clock().isoformat())
        self._pending_session = None
        return self._save()

    def import_activities(self, activities):
        """Store imported activity dicts, skipping ones already stored.
        Returns (added, already_stored, saved). If saving fails nothing is kept in memory either,
        so the screen never shows data that would vanish on the next start."""
        records = [ImportedActivity.from_dict(item) for item in activities]
        before = self._imports.copy()
        added, already = self._imports.merge(records)
        if added and not self._save():
            self._imports = before
            return 0, already, False
        return added, already, True

    def clear_imported(self):
        """Remove every imported activity (logged sessions are kept). Returns True if saved."""
        before = self._imports
        self._imports = ActivityLog()
        if not self._save():
            self._imports = before
            return False
        return True

    def attach_feedback(self, record_id, feedback):
        """Save the coach's review on a logged session (int id) or an imported activity (str id)."""
        record = self._history.find(record_id) or self._imports.find(record_id)
        if record is None:
            return False
        previous = record.to_dict().get("coach_feedback")
        record.set_feedback(feedback)
        if not self._save():
            record.set_feedback(previous)
            return False
        return True

    def reset(self):

        if not self._storage.erase():
            return False
        self._clear_state()
        logger.info("All data reset by the user")
        return True

    # ---- queries ----
    def get_recent_sessions(self, count):
        return [record.to_dict() for record in self._history.recent(count)]

    def sessions_this_week(self, today=None):
        return self._history.count_in_week_of(today or self._clock())

    def query_sessions(self, *filters):
        return [record.to_dict() for record in self._history.query(*filters)]

    @property
    def imported_count(self):
        return len(self._imports)

    def imported_activities(self):
        return self._imports.to_list()

    def imported_in_month(self, year, month):
        return [activity.to_dict() for activity in self._imports.in_month(year, month)]

    def find_record(self, record_id):
        """(kind, dict) for a logged session ('session') or an imported activity ('imported'), else (None, None)."""
        record = self._history.find(record_id)
        if record is not None:
            return "session", record.to_dict()
        activity = self._imports.find(record_id)
        if activity is not None:
            return "imported", activity.to_dict()
        return None, None

    def week_start(self, weeks_ago=0):
        """Monday of this week (0), last week (1), and so on."""
        day = self._clock() - datetime.timedelta(weeks=weeks_ago)
        return day - datetime.timedelta(days=day.weekday())

    def sessions_in_week(self, weeks_ago=0):
        """Sessions logged in this week (0), last week (1), and so on."""
        return self.query_sessions(WeekFilter(self.week_start(weeks_ago)))

    def filter_sessions(self, min_rpe=None, pain_only=False):
        filters = []
        if min_rpe is not None:
            filters.append(MinRpeFilter(min_rpe))
        if pain_only:
            filters.append(PainReportedFilter())
        return self.query_sessions(*filters)
