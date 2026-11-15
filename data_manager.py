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

    @abstractmethod
    def load(self):

    @abstractmethod
    def save(self, payload):

    @abstractmethod
    def quarantine(self):

    @abstractmethod
    def erase(self):


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
