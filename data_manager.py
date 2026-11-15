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
