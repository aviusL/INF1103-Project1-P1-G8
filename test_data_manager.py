import datetime
import json
import os
import tempfile
import unittest

from data_manager import (
    DataManager, InMemoryStorage, JsonFileStorage, LoadStatus, MinRpeFilter,
    PainReportedFilter, Profile, Record, SessionFilter, SessionHistory,
    SessionRecord, Storage,
)

PROFILE = {"age": 30, "fitness_level": "intermediate"}
PLAN = {"exercise_type": "run", "intensity": "medium", "duration_min": 30}


def fixed_clock(year=2026, month=10, day=12):
    return lambda: datetime.date(year, month, day)


class StorageContractTests(unittest.TestCase):

    def make_storages(self, tmp):
        return [JsonFileStorage(os.path.join(tmp, "d.json")), InMemoryStorage()]

    def test_missing_then_round_trip_then_erase(self):
        with tempfile.TemporaryDirectory() as tmp:
            for storage in self.make_storages(tmp):
                self.assertIsInstance(storage, Storage)
                self.assertEqual(storage.load(), (None, LoadStatus.MISSING))
                self.assertTrue(storage.save({"a": 1}))
                self.assertEqual(storage.load(), ({"a": 1}, LoadStatus.OK))
                self.assertTrue(storage.erase())
                self.assertEqual(storage.load()[1], LoadStatus.MISSING)

    def test_storage_cannot_be_instantiated_directly(self):
        with self.assertRaises(TypeError):
            Storage()
        with self.assertRaises(TypeError):
            Record()
        with self.assertRaises(TypeError):
            SessionFilter()


class JsonFileStorageTests(unittest.TestCase):
    def test_corrupt_json_is_backed_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "w") as handle:
                handle.write("{not valid json")
            payload, status = JsonFileStorage(path).load()
            self.assertIsNone(payload)
            self.assertEqual(status, LoadStatus.CORRUPT)
            self.assertFalse(os.path.exists(path))
            self.assertTrue(any(".corrupt-" in n for n in os.listdir(tmp)))

    def test_save_failure_returns_false_not_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "file")
            open(blocker, "w").close()
            # parent "directory" is actually a file -> OSError inside save
            self.assertFalse(JsonFileStorage(os.path.join(blocker, "d.json")).save({"a": 1}))

    def test_erase_removes_tmp_and_corrupt_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            for suffix in ("", ".tmp", ".corrupt-123"):
                with open(path + suffix, "w") as handle:
                    handle.write("x")
            self.assertTrue(JsonFileStorage(path).erase())
            self.assertEqual(os.listdir(tmp), [])


class DataManagerTests(unittest.TestCase):
    def test_load_missing_gives_empty_state(self):
        data = DataManager(InMemoryStorage())
        self.assertEqual(data.load(), LoadStatus.MISSING)
        self.assertEqual(data.load(), "missing")          # str-compatible with io_manager
        self.assertIsNone(data.profile)
        self.assertIsNone(data.pending_session)
        self.assertEqual(data.sessions, [])

    def test_full_round_trip_across_separate_managers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            first = DataManager(JsonFileStorage(path), clock=fixed_clock())
            self.assertTrue(first.save_profile(PROFILE))
            self.assertTrue(first.set_pending_session(PLAN))
            self.assertTrue(first.complete_pending_session({"rpe": 6, "pain_reported": True}))
            second = DataManager(JsonFileStorage(path))
            self.assertEqual(second.load(), LoadStatus.OK)
            self.assertEqual(second.profile, PROFILE)
            self.assertIsNone(second.pending_session)
            self.assertEqual(len(second.filter_sessions(pain_only=True)), 1)
            self.assertEqual(second.sessions[0]["planned"], PLAN)
            self.assertEqual(second.sessions[0]["date"], "2026-10-12")

    def test_phase1_file_format_still_loads(self):
        legacy = {"profile": PROFILE, "pending_session": None,
                  "sessions": [{"id": 1, "date": "2026-10-01", "planned": PLAN, "actual": {"rpe": 5}}]}
        data = DataManager(InMemoryStorage(legacy))
        self.assertEqual(data.load(), LoadStatus.OK)
        self.assertEqual(data.sessions, legacy["sessions"])

    def test_invalid_structure_is_treated_as_corrupt(self):
        bad_payloads = [[], {"sessions": "nope"}, {"sessions": [], "profile": 5},
                        {"sessions": [1, 2]}, {"sessions": [], "pending_session": "x"}]
        for payload in bad_payloads:
            storage = InMemoryStorage(payload)
            data = DataManager(storage)
            self.assertEqual(data.load(), LoadStatus.CORRUPT, payload)
            self.assertIsNone(data.profile)
            self.assertEqual(storage.load()[1], LoadStatus.MISSING)   # quarantined

    def test_invalid_structure_on_disk_is_backed_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            with open(path, "w") as handle:
                json.dump({"sessions": "nope"}, handle)
            self.assertEqual(DataManager(JsonFileStorage(path)).load(), LoadStatus.CORRUPT)
            self.assertTrue(any(".corrupt-" in n for n in os.listdir(tmp)))

    def test_failed_save_reports_false(self):
        data = DataManager(InMemoryStorage(succeed=False))
        self.assertFalse(data.save_profile(PROFILE))
        self.assertFalse(data.set_pending_session(PLAN))

    def test_reset_clears_state_and_failed_reset_keeps_it(self):
        data = DataManager(InMemoryStorage())
        data.save_profile(PROFILE)
        self.assertTrue(data.reset())
        self.assertIsNone(data.profile)
        stuck = DataManager(InMemoryStorage(succeed=True))
        stuck.save_profile(PROFILE)
        stuck._storage._succeed = False
        self.assertFalse(stuck.reset())
        self.assertEqual(stuck.profile, PROFILE)

    def test_returned_values_are_copies(self):
        data = DataManager(InMemoryStorage())
        data.save_profile(PROFILE)
        data.profile["age"] = 99
        self.assertEqual(data.profile["age"], 30)

    def test_session_ids_increment_and_pending_cleared(self):
        data = DataManager(InMemoryStorage(), clock=fixed_clock())
        for _ in range(3):
            data.set_pending_session(PLAN)
            data.complete_pending_session({"rpe": 5})
        self.assertEqual([s["id"] for s in data.sessions], [1, 2, 3])
        self.assertIsNone(data.pending_session)

    def test_get_recent_sessions(self):
        data = DataManager(InMemoryStorage(), clock=fixed_clock())
        for rpe in (1, 2, 3, 4):
            data.complete_pending_session({"rpe": rpe})
        self.assertEqual([s["actual"]["rpe"] for s in data.get_recent_sessions(2)], [3, 4])
        self.assertEqual(data.get_recent_sessions(0), [])
        self.assertEqual(len(data.get_recent_sessions(50)), 4)

    def test_sessions_this_week(self):
        payload = {"profile": None, "pending_session": None, "sessions": [
            {"id": 1, "date": "2026-10-11", "planned": {}, "actual": {}},   # previous Sunday
            {"id": 2, "date": "2026-10-12", "planned": {}, "actual": {}},
            {"id": 3, "date": "2026-10-14", "planned": {}, "actual": {}},
            {"id": 4, "date": "garbage", "planned": {}, "actual": {}},      # skipped
            {"id": 5, "planned": {}, "actual": {}},                         # skipped
        ]}
        data = DataManager(InMemoryStorage(payload))
        data.load()
        self.assertEqual(data.sessions_this_week(today=datetime.date(2026, 10, 12)), 2)

    def test_filters(self):
        data = DataManager(InMemoryStorage(), clock=fixed_clock())
        for actual in ({"rpe": 3}, {"rpe": 8, "pain_reported": True}, {"rpe": 9}, {"rpe": "bad"}, {}):
            data.complete_pending_session(actual)
        self.assertEqual(len(data.filter_sessions()), 5)
        self.assertEqual([s["id"] for s in data.filter_sessions(min_rpe=8)], [2, 3])
        self.assertEqual([s["id"] for s in data.filter_sessions(pain_only=True)], [2])
        self.assertEqual([s["id"] for s in data.query_sessions(MinRpeFilter(8), PainReportedFilter())], [2])

    def test_custom_filter_needs_no_changes_to_history(self):
        class OddIdFilter(SessionFilter):
            def matches(self, record):
                return record.id % 2 == 1
        history = SessionHistory([SessionRecord(i, "2026-10-12", {}, {}) for i in range(1, 5)])
        self.assertEqual([r.id for r in history.query(OddIdFilter())], [1, 3])


class RecordTests(unittest.TestCase):
    def test_polymorphic_to_dict_and_equality(self):
        records = [Profile(PROFILE), SessionRecord(1, "2026-10-12", PLAN, {"rpe": 5})]
        for record in records:
            self.assertIsInstance(record, Record)
            self.assertEqual(type(record).from_dict(record.to_dict()), record)

    def test_from_dict_rejects_non_dicts(self):
        for cls in (Profile, SessionRecord):
            with self.assertRaises(ValueError):
                cls.from_dict("nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
