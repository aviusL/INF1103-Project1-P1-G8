import json
import logging
import os
import time

logger = logging.getLogger("data_manager")

DATA_FILE = os.environ.get("FITNESS_DATA_FILE", "data/fitness_data.json")
LOG_FILE = os.environ.get("FITNESS_LOG_FILE", "data/app.log")


def setup_logging():
    """Send log output to a file so nothing is printed outside io_manager."""
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
    """Move a corrupt file aside so it is never silently overwritten."""
    backup = "{}.corrupt-{}".format(path, int(time.time()))
    try:
        os.replace(path, backup)
        logger.error("Corrupt data file moved to %s", backup)
    except OSError as exc:
        logger.error("Could not back up corrupt file %s: %s", path, exc)
