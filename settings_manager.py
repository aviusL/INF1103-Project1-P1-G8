"""settings_manager.py - App settings: Claude API key and model, colour theme, display preferences.

Settings live in their own small JSON file (data/settings.json by default), separate from the
training data, so "Reset all data" never deletes the API key or the theme.

Nothing here imports Flet: the colour maths and every validation rule can be unit-tested
without a GUI, and the console version shares the saved API key.
"""
import datetime
import logging
import os
import re
from dataclasses import asdict, dataclass, field, replace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from ai_manager import DEFAULT_MODEL, MODEL_IDS
from data_manager import DataConfig, InMemoryStorage, JsonFileStorage, LoadStatus

logger = logging.getLogger("settings_manager")

SETTINGS_VERSION = 1
WEEK_STARTS = ("monday", "sunday")

# WCAG 2.1 contrast minimums: 4.5:1 for body text, 3:1 for icons, buttons and other UI parts.
MIN_TEXT_CONTRAST = 4.5
MIN_ACCENT_CONTRAST = 3.0

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


# --------------------------------------------------------------------------- #
# Time zones
# --------------------------------------------------------------------------- #
def valid_timezone(name):
    """True for '' (use the computer's own clock) or an IANA name such as 'Asia/Singapore'."""
    if not name:
        return True
    try:
        ZoneInfo(str(name))
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def timezone_choices():
    """IANA names for the Settings dropdown, e.g. ['Africa/Abidjan', ..., 'UTC']."""
    names = {name for name in available_timezones() if "/" in name and not name.startswith(("Etc/", "posix", "right"))}
    return sorted(names | {"UTC"})


# --------------------------------------------------------------------------- #
# Colour helpers
# --------------------------------------------------------------------------- #
def normalize_hex(value):
    """'a3e635', '#A3E635' or '#ae5' -> '#A3E635'. Returns None if the value is not a hex colour."""
    match = _HEX.match(str(value or "").strip())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits.upper()


def _rgb(hex_colour):
    value = normalize_hex(hex_colour)
    if value is None:
        raise ValueError("not a hex colour: {!r}".format(hex_colour))
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def mix(first, second, amount):
    """Blend two hex colours: amount 0 gives first, 1 gives second."""
    a, b = _rgb(first), _rgb(second)
    return "#{:02X}{:02X}{:02X}".format(*(round(x + (y - x) * amount) for x, y in zip(a, b)))


def luminance(hex_colour):
    """Relative luminance (WCAG 2.1), 0 for black to 1 for white."""
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first, second):
    """WCAG contrast ratio between two colours, from 1 (identical) to 21 (black on white)."""
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def readable_on(background):
    """Black or white, whichever is easier to read on the given colour."""
    return "#000000" if contrast_ratio(background, "#000000") >= contrast_ratio(background, "#FFFFFF") else "#FFFFFF"


# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ThemeColors:
    """The four colours a user picks. Every other shade in the GUI is derived from these."""
    accent: str = "#A3E635"       # lime green: buttons, progress, highlights
    background: str = "#0C0F0A"   # page background
    card: str = "#181D15"         # cards and panels
    text: str = "#EEF3E8"         # main text

    FIELDS = ("accent", "background", "card", "text")
    LABELS = {"accent": "Accent", "background": "Background", "card": "Cards", "text": "Text"}

    @classmethod
    def from_dict(cls, data):
        """Build from stored data. Missing or invalid colours fall back to the default."""
        default = cls()
        data = data if isinstance(data, dict) else {}
        values = {}
        for name in cls.FIELDS:
            values[name] = normalize_hex(data.get(name)) or getattr(default, name)
        return cls(**values)

    def to_dict(self):
        return {name: getattr(self, name) for name in self.FIELDS}

    def problems(self):
        """Reasons this theme would be hard to read; an empty list means it can be used."""
        errors = []
        for name in self.FIELDS:
            if normalize_hex(getattr(self, name)) is None:
                errors.append("{} must be a hex colour like #A3E635".format(self.LABELS[name].lower()))
        if errors:
            return errors
        for surface in ("background", "card"):
            ratio = contrast_ratio(self.text, getattr(self, surface))
            if ratio < MIN_TEXT_CONTRAST:
                errors.append("text on {} is too faint ({:.1f}:1, needs {}:1)".format(
                    self.LABELS[surface].lower(), ratio, MIN_TEXT_CONTRAST))
        for surface in ("background", "card"):
            ratio = contrast_ratio(self.accent, getattr(self, surface))
            if ratio < MIN_ACCENT_CONTRAST:
                errors.append("accent on {} is too faint ({:.1f}:1, needs {}:1)".format(
                    self.LABELS[surface].lower(), ratio, MIN_ACCENT_CONTRAST))
        return errors

    @property
    def is_dark(self):
        return luminance(self.background) < 0.4


DEFAULT_THEME = ThemeColors()

# Ready-made themes for the Settings screen. All pass ThemeColors.problems().
THEME_PRESETS = {
    "Lime": DEFAULT_THEME,
    "Ocean": ThemeColors(accent="#38BDF8", background="#0A0E14", card="#151B24", text="#E6EDF5"),
    "Ember": ThemeColors(accent="#FF6A2B", background="#0E0E10", card="#1F1F23", text="#F4F4F5"),
    "Violet": ThemeColors(accent="#B39DFF", background="#0F0C14", card="#1C1724", text="#EEEAF5"),
}


# --------------------------------------------------------------------------- #
# API key helpers
# --------------------------------------------------------------------------- #
def clean_api_key(raw):
    """Undo common copy-paste mistakes: spaces, quotes, or a whole 'ANTHROPIC_API_KEY=...' line."""
    key = str(raw or "").strip().strip("'\"").strip()
    if key.upper().startswith("ANTHROPIC_API_KEY="):
        key = key.split("=", 1)[1].strip().strip("'\"").strip()
    return key


def api_key_problem(key):
    """Why this doesn't look like an Anthropic API key, or None if it does (or is empty)."""
    if not key:
        return None
    if re.search(r"\s", key):
        return "the API key contains spaces or line breaks"
    if not key.startswith("sk-ant-"):
        return "Anthropic API keys start with sk-ant-"
    if len(key) < 30:
        return "the API key looks too short; copy the whole key"
    return None


def mask_key(key):
    """'sk-ant-api03-....AbCd' -> 'sk-ant-…AbCd', so a saved key is never shown in full."""
    if not key:
        return ""
    return key[:7] + "…" + key[-4:] if len(key) > 14 else "…" + key[-4:]


# --------------------------------------------------------------------------- #
# Settings value object
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AppSettings:
    """Everything on the Settings screen. Immutable: change it with dataclasses.replace()."""
    api_key: str = ""                  # empty = use ANTHROPIC_API_KEY from the environment
    model: str = DEFAULT_MODEL
    theme: ThemeColors = field(default_factory=ThemeColors)
    week_starts_on: str = "monday"
    show_splash: bool = True
    animations: bool = True
    timezone: str = ""                 # IANA name; empty = the server's own clock (UTC in Docker)

    @classmethod
    def from_dict(cls, data, default_model=DEFAULT_MODEL):
        """Build from stored data, keeping every valid value and defaulting the rest.
        Never raises: a hand-edited or old settings file still loads."""
        data = data if isinstance(data, dict) else {}
        key = clean_api_key(data.get("api_key"))
        model = data.get("model")
        week = str(data.get("week_starts_on") or "").lower()
        timezone = str(data.get("timezone") or "").strip()
        theme = ThemeColors.from_dict(data.get("theme"))
        if theme.problems():          # e.g. a hand-edited file with unreadable colours
            logger.warning("Stored theme is hard to read (%s); using the default", "; ".join(theme.problems()))
            theme = DEFAULT_THEME
        return cls(
            api_key=key if api_key_problem(key) is None else "",
            model=model if model in MODEL_IDS else (default_model if default_model in MODEL_IDS else DEFAULT_MODEL),
            theme=theme,
            week_starts_on=week if week in WEEK_STARTS else "monday",
            show_splash=data.get("show_splash") if isinstance(data.get("show_splash"), bool) else True,
            animations=data.get("animations") if isinstance(data.get("animations"), bool) else True,
            timezone=timezone if valid_timezone(timezone) else "",
        )

    def to_dict(self):
        data = asdict(self)
        data["theme"] = self.theme.to_dict()
        data["version"] = SETTINGS_VERSION
        return data

    def problems(self):
        errors = []
        key_error = api_key_problem(self.api_key)
        if key_error:
            errors.append(key_error)
        if self.model not in MODEL_IDS:
            errors.append("choose one of the listed Claude models")
        if self.week_starts_on not in WEEK_STARTS:
            errors.append("the week must start on Monday or Sunday")
        if not valid_timezone(self.timezone):
            errors.append("unknown time zone: choose one from the list, like Asia/Singapore")
        if not isinstance(self.show_splash, bool) or not isinstance(self.animations, bool):
            errors.append("switch settings must be on or off")
        return errors + self.theme.problems()

    @property
    def masked_key(self):
        return mask_key(self.api_key)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
class PrivateJsonFileStorage(JsonFileStorage):
    """JsonFileStorage that keeps the file readable by its owner only (it can hold an API key).
    On Windows chmod can only toggle read-only, so it is best-effort there."""

    def save(self, payload):
        if not super().save(payload):
            return False
        try:
            os.chmod(self.path, 0o600)
        except OSError as exc:
            logger.warning("Could not restrict permissions on %s: %s", self.path, exc)
        return True


class SettingsManager:
    """Loads, validates and saves AppSettings. Depends only on the Storage interface."""

    def __init__(self, storage, environ=None, default_model=DEFAULT_MODEL, default_timezone="", utcnow=None):
        self._storage = storage           # association: any Storage implementation works
        self._environ = os.environ if environ is None else environ
        self._default_model = default_model
        self._default_timezone = default_timezone if valid_timezone(default_timezone) else ""
        self._utcnow = utcnow or (lambda: datetime.datetime.now(datetime.timezone.utc))   # injectable for tests
        self._settings = AppSettings(model=default_model if default_model in MODEL_IDS else DEFAULT_MODEL)
        self._status = LoadStatus.MISSING

    @classmethod
    def from_env(cls, config=None):
        """Settings file from AEGISFIT_SETTINGS_FILE, otherwise settings.json beside the data file."""
        config = config or DataConfig.from_env()
        path = os.environ.get("AEGISFIT_SETTINGS_FILE") or os.path.join(
            os.path.dirname(config.data_file) or ".", "settings.json")
        return cls(PrivateJsonFileStorage(path), default_model=os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL),
                   default_timezone=os.environ.get("AEGISFIT_TIMEZONE") or os.environ.get("TZ") or "")

    @classmethod
    def in_memory(cls, environ=None):
        """Settings that are never written to disk (tests, or a service built by hand)."""
        return cls(InMemoryStorage(), environ=environ)

    @property
    def settings(self):
        return self._settings

    @property
    def status(self):
        """LoadStatus of the last load(): ok, missing (first run) or corrupt (backed up, defaults used)."""
        return self._status

    @property
    def location(self):
        return getattr(self._storage, "path", "not saved to disk")

    def load(self):
        payload, status = self._storage.load()
        self._status = status
        if status == LoadStatus.OK:
            self._settings = AppSettings.from_dict(payload, self._default_model)
        return status

    def save(self, settings):
        """Validate and persist. Returns a list of problems (empty on success);
        on failure the current settings are left unchanged."""
        if not isinstance(settings, AppSettings):
            return ["settings must be an AppSettings object"]
        settings = replace(settings, api_key=clean_api_key(settings.api_key),
                           theme=ThemeColors(**{name: normalize_hex(getattr(settings.theme, name)) or ""
                                                for name in ThemeColors.FIELDS}))
        errors = settings.problems()
        if errors:
            return errors
        if not self._storage.save(settings.to_dict()):
            return ["could not save the settings file"]
        self._settings = settings
        return []

    def environment_key_problem(self):
        """Why ANTHROPIC_API_KEY doesn't look like a key (for example a broken shell command), or None."""
        key = clean_api_key(self._environ.get("ANTHROPIC_API_KEY"))
        return api_key_problem(key) if key else None

    # ---- clock ----
    @property
    def timezone_name(self):
        """The zone 'today' is worked out in: the one chosen in Settings, else AEGISFIT_TIMEZONE / TZ,
        else '' for the computer's own clock."""
        return self._settings.timezone or self._default_timezone

    def now(self):
        """The current time as an aware datetime in the chosen zone."""
        utc = self._utcnow()
        name = self.timezone_name
        return utc.astimezone(ZoneInfo(name)) if name else utc.astimezone()

    def today(self):
        """Today's date where the user is. This is the clock the whole app uses, so a Docker
        container running on UTC still gives the right day for someone in Singapore."""
        return self.now().date()

    def api_key_source(self):
        """Where the key in use comes from: 'settings', 'environment', or None if there is no key."""
        if self._settings.api_key:
            return "settings"
        if str(self._environ.get("ANTHROPIC_API_KEY") or "").strip():
            return "environment"
        return None


__all__ = [
    "AppSettings", "SettingsManager", "PrivateJsonFileStorage", "ThemeColors", "DEFAULT_THEME", "THEME_PRESETS",
    "WEEK_STARTS", "normalize_hex", "mix", "luminance", "contrast_ratio", "readable_on", "clean_api_key",
    "api_key_problem", "mask_key", "valid_timezone", "timezone_choices",
]
