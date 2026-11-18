"""activity_import.py - read training history exported from other apps (part of the Data Manager's job).

An experienced athlete already has years of activities in Strava. Strava's account archive
contains activities.csv, one row per activity; StravaCsvImporter turns that file into plain
activity dicts that DataManager stores next to the coach's own sessions.

Imported activities are history, not plans: they are shown on the calendar with their key
stats and are not sent to the AI when they are imported. (Coach feedback on one session is
asked for separately, only when the user presses the button.)

Adding another source (Garmin, a .fit file) means adding one ActivityImporter subclass;
nothing that stores or shows activities has to change (Open/Closed Principle).
"""
import csv
import datetime
import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

MAX_IMPORT_BYTES = 15 * 1024 * 1024       # the web server accepts 16 MiB per message; years of activities.csv is a few MB
MAX_SKIP_DETAILS = 5                      # row problems listed back to the user

STRAVA_HELP_URL = "https://support.strava.com/hc/en-us/articles/216918437-Exporting-your-Data-and-Bulk-Export"

# Strava's activity types -> the activity names AegisFit uses (icons, filters, prompts).
STRAVA_ACTIVITY_TYPES = {
    "run": "running", "trail run": "running", "virtual run": "running", "treadmill": "running",
    "ride": "cycling", "virtual ride": "cycling", "mountain bike ride": "cycling", "gravel ride": "cycling",
    "e-bike ride": "cycling", "e-mountain bike ride": "cycling", "velomobile": "cycling", "handcycle": "cycling",
    "swim": "swimming",
    "walk": "walking", "hike": "walking",
    "weight training": "strength training",
    "hiit": "hiit", "high intensity interval training": "hiit", "crossfit": "hiit",
    "rowing": "rowing", "virtual row": "rowing", "indoor rowing": "rowing",
}
PACE_ACTIVITIES = ("running", "walking")  # shown as min/km; other activities as km/h
SPEED_ACTIVITIES = ("cycling",)

_REQUIRED_COLUMNS = ("Activity ID", "Activity Date", "Activity Type")
_SPACES = re.compile(r"[   ]")   # Strava writes a narrow no-break space before AM/PM
_DATE_FORMATS = (
    "%b %d, %Y, %I:%M:%S %p",    # Jan 5, 2024, 6:15:30 AM (current exports)
    "%b %d, %Y, %H:%M:%S",
    "%d %b %Y, %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
)


@dataclass(frozen=True)
class ImportResult:
    """What an importer read. When error is set, nothing could be read at all."""
    activities: tuple = ()
    skipped: tuple = ()                    # (row number, reason) for rows that were left out
    error: Optional[str] = None

    @property
    def ok(self):
        return self.error is None


class ActivityImporter(ABC):
    """Interface for anything that turns an exported file into activity dicts.

    Contract for every implementation: never raise on bad input; return an ImportResult
    whose error says what is wrong with the file, and skip (and list) single bad rows.
    Dates come back in the user's time zone, because the calendar shows local days.
    """

    source = ""

    @abstractmethod
    def parse(self, data, tz):
        """Read raw file bytes. tz is the user's tzinfo. Returns an ImportResult."""

    @staticmethod
    def decode(data):
        """File bytes as text: UTF-8 (with or without a byte-order mark), else Windows-1252."""
        if isinstance(data, str):
            return data
        try:
            return bytes(data).decode("utf-8-sig")
        except UnicodeDecodeError:
            return bytes(data).decode("cp1252", errors="replace")


def parse_number(text):
    """A float from an export cell, or None. Accepts '1,234.5', '10,02' (decimal comma) and ''."""
    text = str(text or "").strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"-?\d+,\d+", text):
        text = text.replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None   # drop NaN / inf


def parse_utc_time(text):
    """Strava's 'Activity Date' (UTC) as an aware datetime, or None if no known format matches."""
    text = _SPACES.sub(" ", str(text or "")).strip()
    for pattern in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, pattern).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    return None


class _Row:
    """One CSV row, read by column name. Strava repeats some names (Distance, Elapsed Time):
    the first copy is the display value (km, whole seconds), the last the raw one (metres)."""

    def __init__(self, cells, columns):
        self._cells = cells
        self._columns = columns

    def _values(self, name):
        return [self._cells[i].strip() for i in self._columns.get(name, ()) if i < len(self._cells)]

    def text(self, name):
        values = [value for value in self._values(name) if value]
        return values[0] if values else ""

    def number(self, name, last=False):
        """The first column of that name with a number in it; last=True reads only the last copy."""
        values = self._values(name)
        if last:
            return parse_number(values[-1]) if values else None
        numbers = [n for n in (parse_number(v) for v in values) if n is not None]
        return numbers[0] if numbers else None

    def copies(self, name):
        return len(self._columns.get(name, ()))


def _rounded(value, digits=1):
    return None if value is None else round(value, digits)


def _whole(value):
    return None if value is None else int(round(value))


class StravaCsvImporter(ActivityImporter):
    """Reads activities.csv from a Strava account archive (Settings > My Account >
    Download or Delete Your Account > Request your archive)."""

    source = "strava"

    def parse(self, data, tz):
        if data is None or len(data) == 0:
            return ImportResult(error="the file is empty")
        if len(data) > MAX_IMPORT_BYTES:
            return ImportResult(error="the file is larger than {} MB, so it is not Strava's activities.csv"
                                .format(MAX_IMPORT_BYTES // (1024 * 1024)))
        try:
            rows = list(csv.reader(io.StringIO(self.decode(data), newline="")))
        except csv.Error as exc:
            return ImportResult(error="the file is not a readable CSV file ({})".format(exc))
        rows = [row for row in rows if any(cell.strip() for cell in row)]
        if not rows:
            return ImportResult(error="the file is empty")
        columns = {}
        for index, name in enumerate(rows[0]):
            columns.setdefault(name.strip(), []).append(index)
        missing = [name for name in _REQUIRED_COLUMNS if name not in columns]
        if missing:
            return ImportResult(error="this does not look like Strava's activities.csv (no '{}' column). "
                                      "Upload activities.csv from inside your Strava archive."
                                .format(missing[0]))
        activities, skipped, seen = [], [], set()
        for number, cells in enumerate(rows[1:], start=2):     # row 1 is the header
            activity, problem = self._activity(_Row(cells, columns), tz)
            if problem:
                skipped.append((number, problem))
            elif activity["id"] in seen:
                skipped.append((number, "the same activity appears twice in the file"))
            else:
                seen.add(activity["id"])
                activities.append(activity)
        if not activities and skipped:
            return ImportResult(skipped=tuple(skipped), error="no activity in the file could be read ({})"
                                .format(skipped[0][1]))
        activities.sort(key=lambda a: (a["date"], a.get("start_time", "")))
        return ImportResult(tuple(activities), tuple(skipped))

    def _activity(self, row, tz):
        """(activity dict, None) for a usable row, else (None, why it was skipped)."""
        external_id = row.text("Activity ID")
        if not external_id:
            return None, "no activity ID"
        started = parse_utc_time(row.text("Activity Date"))
        if started is None:
            return None, "unreadable date '{}'".format(row.text("Activity Date"))
        local = started.astimezone(tz)
        sport = row.text("Activity Type") or "Workout"
        activity = STRAVA_ACTIVITY_TYPES.get(sport.lower(), sport.lower())

        moving_s = row.number("Moving Time")
        elapsed_s = row.number("Elapsed Time")
        seconds = moving_s or elapsed_s
        if not seconds or seconds <= 0:
            return None, "no duration"
        distance_km = self._distance_km(row, seconds)

        record = {
            "id": "{}-{}".format(self.source, external_id),
            "source": self.source,
            "external_id": external_id,
            "date": local.date().isoformat(),
            "start_time": local.strftime("%H:%M"),
            "name": row.text("Activity Name") or sport,
            "activity": activity,
            "sport_type": sport,
            "duration_min": _rounded(seconds / 60),
            "elapsed_min": _rounded(elapsed_s / 60) if elapsed_s else None,
            "distance_km": _rounded(distance_km, 2) if distance_km else None,
            "avg_hr_bpm": _whole(row.number("Average Heart Rate")),
            "max_hr_bpm": _whole(row.number("Max Heart Rate")),
            "elevation_gain_m": _whole(row.number("Elevation Gain")),
            "calories": _whole(row.number("Calories")),
            "relative_effort": _whole(row.number("Relative Effort")),
            "perceived_exertion": _whole(row.number("Perceived Exertion")),
            "description": row.text("Activity Description")[:500],
        }
        if distance_km and distance_km >= 0.1:
            if activity in PACE_ACTIVITIES:
                record["avg_pace_min_per_km"] = _rounded(seconds / 60 / distance_km, 2)
            elif activity in SPEED_ACTIVITIES:
                record["avg_speed_kmh"] = _rounded(distance_km / (seconds / 3600))
        pe = record["perceived_exertion"]
        if pe is not None and not 1 <= pe <= 10:
            record["perceived_exertion"] = None
        return {key: value for key, value in record.items() if value not in (None, "")}, None

    @staticmethod
    def _distance_km(row, seconds):
        """Distance in km. With two Distance columns the last is metres; a single one is km,
        unless reading it as km would mean an impossible speed (then it was metres)."""
        if row.copies("Distance") >= 2:
            metres = row.number("Distance", last=True)
            if metres is not None:
                return metres / 1000
        value = row.number("Distance")
        if value is None or value <= 0:
            return None
        if value / (seconds / 3600) > 200:       # faster than 200 km/h: this was metres
            return value / 1000
        return value


IMPORTERS = {StravaCsvImporter.source: StravaCsvImporter}

__all__ = ["ActivityImporter", "StravaCsvImporter", "ImportResult", "IMPORTERS", "STRAVA_HELP_URL",
           "MAX_IMPORT_BYTES", "MAX_SKIP_DETAILS", "parse_number", "parse_utc_time"]
