"""data_manager.py - Data layer: flat-file (JSON) persistence. No console output, no AI calls."""
import datetime
import glob
import json
import logging
import os
import time

logger = logging.getLogger("data_manager")

DATA_FILE = os.environ.get("FITNESS_DATA_FILE", "data/fitness_data.json")
LOG_FILE = os.environ.get("FITNESS_LOG_FILE", "data/app.log")


def setup_logging():
    """Send log output to a file so nothing is printed outside io_manager."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
        logging.basicConfig(
            filename=LOG_FILE,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    except OSError:
        # A log file is nice to have, never worth crashing for: run without logging.
        logging.disable(logging.CRITICAL)


def empty_store():
    return {"profile": None, "pending_session": None, "sessions": []}


def is_valid_store(store):
    if not isinstance(store, dict):
        return False
    if not isinstance(store.get("sessions"), list):
        return False
    profile = store.get("profile")
    if profile is not None and not isinstance(profile, dict):
        return False
    return True


def backup_corrupt_file(path):
    """Move a corrupt file aside so it is never silently overwritten."""
    backup = "{}.corrupt-{}".format(path, int(time.time()))
    try:
        os.replace(path, backup)
        logger.error("Corrupt data file moved to %s", backup)
    except OSError as exc:
        logger.error("Could not back up corrupt file %s: %s", path, exc)


def load_data(path=None):
    """Return (store, status). status is 'ok', 'missing' or 'corrupt'. Never raises."""
    path = path or DATA_FILE
    if not os.path.exists(path):
        logger.info("No data file at %s; starting fresh", path)
        return empty_store(), "missing"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            store = json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.error("Failed to read %s: %s", path, exc)
        backup_corrupt_file(path)
        return empty_store(), "corrupt"
    if not is_valid_store(store):
        logger.error("Data file %s has an invalid structure", path)
        backup_corrupt_file(path)
        return empty_store(), "corrupt"
    store.setdefault("pending_session", None)
    return store, "ok"


def save_data(store, path=None):
    """Write atomically (temp file + replace). Returns True on success."""
    path = path or DATA_FILE
    tmp_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
        return True
    except OSError as exc:
        logger.error("Failed to save %s: %s", path, exc)
        return False


def save_profile(store, profile, path=None):
    store["profile"] = profile
    return save_data(store, path)


def set_pending_session(store, suggestion, path=None):
    store["pending_session"] = suggestion
    return save_data(store, path)


def complete_pending_session(store, actual, path=None):
    """Combine the planned session with how it went, append to history."""
    record = {
        "id": len(store["sessions"]) + 1,
        "date": time.strftime("%Y-%m-%d"),
        "planned": store["pending_session"],
        "actual": actual,
    }
    store["sessions"].append(record)
    store["pending_session"] = None
    return save_data(store, path)


def reset_data(store, path=None):
    """Delete the data file (plus temp and .corrupt backups) and empty the in-memory store.
    The store is emptied in place so every reference to it stays valid.
    Returns True only if every file was removed; on failure the store is left unchanged."""
    path = path or DATA_FILE
    targets = [path, path + ".tmp"] + glob.glob(glob.escape(path) + ".corrupt-*")
    all_removed = True
    for target in targets:
        try:
            os.remove(target)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.error("Could not delete %s: %s", target, exc)
            all_removed = False
    if all_removed:
        store.clear()
        store.update(empty_store())
        logger.info("All data reset by the user")
    return all_removed


def get_recent_sessions(store, count):
    """The last `count` sessions - this is what is sent to the AI as history."""
    if count <= 0:
        return []
    return store["sessions"][-count:]


def sessions_this_week(store, today=None):
    """How many sessions were logged in the current calendar week (Monday to Sunday).
    `today` is only passed in by tests; records with an unreadable date are skipped."""
    today = today or datetime.date.today()
    this_week = tuple(today.isocalendar())[:2]
    count = 0
    for record in store["sessions"]:
        try:
            logged = datetime.date.fromisoformat(record["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if tuple(logged.isocalendar())[:2] == this_week:
            count += 1
    return count


def filter_sessions(store, min_rpe=None, pain_only=False):
    """Query function: sessions at/above an RPE and/or with pain reported."""
    results = []
    for record in store["sessions"]:
        actual = record.get("actual", {})
        if min_rpe is not None and actual.get("rpe", 0) < min_rpe:
            continue
        if pain_only and not actual.get("pain_reported", False):
            continue
        results.append(record)
    return results
