import json
import logging
import os
import time

logger = logging.getLogger("data_manager")

DATA_FILE = os.environ.get("FITNESS_DATA_FILE", "data/fitness_data.json")
LOG_FILE = os.environ.get("FITNESS_LOG_FILE", "data/app.log")


def setup_logging():
    #Sends log output to file so that nothing is printed outside io_manager.
    os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


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
    #Move a corrupt file aside so it is never silently overwritten.
    backup = "{}.corrupt-{}".format(path, int(time.time()))
    try:
        os.replace(path, backup)
        logger.error("Corrupt data file moved to %s", backup)
    except OSError as exc:
        logger.error("Could not back up corrupt file %s: %s", path, exc)


def load_data(path=None):
    #Checks for json file to read, and ensures that the file is not corrupted
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
    #Writes a temporary file (.tmp) and replaced the original file with the temporary file. Returns True on success.
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
        
        
def set_pending_session(store, suggestion, path=None):
    store["pending_session"] = suggestion
    return save_data(store, path)


def complete_pending_session(store, actual, path=None):
    #Combines the planned session with how it went, append to history.
    record = {
        "id": len(store["sessions"]) + 1,
        "date": time.strftime("%Y-%m-%d"),
        "planned": store["pending_session"],
        "actual": actual,
    }
    store["sessions"].append(record)
    store["pending_session"] = None
    return save_data(store, path)


def get_recent_sessions(store, count):
    #The last `count` sessions - this is what how the AI retreives the recent workouts
    if count <= 0:
        return []
    return store["sessions"][-count:]


def filter_sessions(store, min_rpe=None, pain_only=False):
    #Query function: sessions at/above an RPE and/or with pain reported.
    results = []
    for record in store["sessions"]:
        actual = record.get("actual", {})
        if min_rpe is not None and actual.get("rpe", 0) < min_rpe:
            continue
        if pain_only and not actual.get("pain_reported", False):
            continue
        results.append(record)
    return results
