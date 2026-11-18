"""Tests for the Strava activities.csv import (activity_import.py) and how imported history is
stored (DataManager) and shown (CoachService calendar events). No network, no AI."""
import csv
import datetime
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from zoneinfo import ZoneInfo

from activity_import import ImportResult, StravaCsvImporter, parse_number, parse_utc_time
from data_manager import ActivityLog, DataManager, ImportedActivity, InMemoryStorage, JsonFileStorage, LoadStatus

SGT = ZoneInfo("Asia/Singapore")

# Real Strava exports repeat several column names: the first Distance is km, the second metres;
# the first Elapsed Time is whole seconds, the second has decimals.
HEADER = ["Activity ID", "Activity Date", "Activity Name", "Activity Type", "Activity Description",
          "Elapsed Time", "Distance", "Max Heart Rate", "Relative Effort", "Commute", "Activity Gear",
          "Filename", "Elapsed Time", "Moving Time", "Distance", "Max Speed", "Average Speed",
          "Elevation Gain", "Max Heart Rate", "Average Heart Rate", "Calories", "Perceived Exertion"]


def strava_row(activity_id="101", date="Jan 5, 2024, 11:15:30 PM", name="Morning Run", kind="Run",
               elapsed="3300", km="10.02", metres="10020.0", moving="3120", avg_hr="152", max_hr="178",
               elevation="85.0", calories="640", effort="72", pe="", description=""):
    values = {
        0: activity_id, 1: date, 2: name, 3: kind, 4: description, 5: elapsed, 6: km, 7: max_hr, 8: effort,
        9: "false", 10: "", 11: "activities/{}.fit".format(activity_id), 12: elapsed + ".0", 13: moving,
        14: metres, 15: "", 16: "", 17: elevation, 18: max_hr, 19: avg_hr, 20: calories, 21: pe,
    }
    return [values[i] for i in range(len(HEADER))]


def strava_csv(*rows, header=HEADER, encoding="utf-8"):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode(encoding)


class NumberAndDateParsingTest(unittest.TestCase):
    def test_numbers_in_export_formats(self):
        self.assertEqual(parse_number("10.02"), 10.02)
        self.assertEqual(parse_number("1,234.5"), 1234.5)
        self.assertEqual(parse_number("10,02"), 10.02)          # decimal comma
        self.assertIsNone(parse_number(""))
        self.assertIsNone(parse_number("n/a"))
        self.assertIsNone(parse_number("nan"))

    def test_strava_dates_are_utc(self):
        moment = parse_utc_time("Jan 5, 2024, 11:15:30 PM")
        self.assertEqual(moment, datetime.datetime(2024, 1, 5, 23, 15, 30, tzinfo=datetime.timezone.utc))

    def test_narrow_no_break_space_before_am_pm(self):
        self.assertIsNotNone(parse_utc_time("Jan 5, 2024, 6:15:30 AM"))

    def test_other_date_formats_and_garbage(self):
        self.assertIsNotNone(parse_utc_time("2024-01-05 06:15:30"))
        self.assertIsNotNone(parse_utc_time("2024-01-05T06:15:30Z"))
        self.assertIsNone(parse_utc_time("yesterday"))


class StravaImporterTest(unittest.TestCase):
    def parse(self, data, tz=SGT):
        return StravaCsvImporter().parse(data, tz)

    def test_reads_key_stats(self):
        result = self.parse(strava_csv(strava_row()))
        self.assertTrue(result.ok)
        [run] = result.activities
        self.assertEqual(run["id"], "strava-101")
        self.assertEqual(run["source"], "strava")
        self.assertEqual(run["activity"], "running")
        self.assertEqual(run["sport_type"], "Run")
        self.assertEqual(run["distance_km"], 10.02)        # from the metres column
        self.assertEqual(run["duration_min"], 52.0)        # moving time
        self.assertEqual(run["elapsed_min"], 55.0)
        self.assertEqual(run["avg_hr_bpm"], 152)
        self.assertEqual(run["max_hr_bpm"], 178)
        self.assertEqual(run["elevation_gain_m"], 85)
        self.assertEqual(run["calories"], 640)
        self.assertEqual(run["relative_effort"], 72)
        self.assertAlmostEqual(run["avg_pace_min_per_km"], 5.19, places=2)

    def test_utc_evening_is_next_day_in_singapore(self):
        # 23:15 UTC on 5 Jan is 07:15 on 6 Jan in Singapore: the calendar must show the 6th.
        [run] = self.parse(strava_csv(strava_row())).activities
        self.assertEqual(run["date"], "2024-01-06")
        self.assertEqual(run["start_time"], "07:15")
        [same_run_in_london] = self.parse(strava_csv(strava_row()), tz=ZoneInfo("Europe/London")).activities
        self.assertEqual(same_run_in_london["date"], "2024-01-05")

    def test_activity_types_are_mapped(self):
        rows = [strava_row(activity_id=str(i), kind=kind) for i, kind in
                enumerate(("Ride", "Swim", "Walk", "Weight Training", "Yoga", "Virtual Ride"))]
        kinds = [a["activity"] for a in self.parse(strava_csv(*rows)).activities]
        self.assertEqual(sorted(kinds), sorted(["cycling", "swimming", "walking", "strength training", "yoga",
                                                "cycling"]))

    def test_rides_get_speed_not_pace(self):
        [ride] = self.parse(strava_csv(strava_row(kind="Ride", metres="40000", moving="5400"))).activities
        self.assertEqual(ride["avg_speed_kmh"], 26.7)
        self.assertNotIn("avg_pace_min_per_km", ride)

    def test_missing_stats_are_left_out(self):
        [workout] = self.parse(strava_csv(strava_row(kind="Workout", km="", metres="", avg_hr="", max_hr="",
                                                     elevation="", calories="", effort=""))).activities
        for key in ("distance_km", "avg_hr_bpm", "max_hr_bpm", "calories", "avg_pace_min_per_km"):
            self.assertNotIn(key, workout)
        self.assertEqual(workout["duration_min"], 52.0)

    def test_blank_metres_column_falls_back_to_km(self):
        [run] = self.parse(strava_csv(strava_row(metres=""))).activities
        self.assertEqual(run["distance_km"], 10.02)

    def test_perceived_exertion_kept_only_when_valid(self):
        good, bad = self.parse(strava_csv(strava_row(activity_id="1", pe="7"),
                                          strava_row(activity_id="2", pe="40", date="Jan 7, 2024, 1:00:00 AM"))
                               ).activities
        self.assertEqual(good["perceived_exertion"], 7)
        self.assertNotIn("perceived_exertion", bad)

    def test_bad_rows_are_skipped_and_reported(self):
        result = self.parse(strava_csv(strava_row(activity_id="1"),
                                       strava_row(activity_id="2", date="not a date"),
                                       strava_row(activity_id="", date="Jan 9, 2024, 1:00:00 AM"),
                                       strava_row(activity_id="4", moving="", elapsed="0")))
        self.assertTrue(result.ok)
        self.assertEqual(len(result.activities), 1)
        self.assertEqual([number for number, _ in result.skipped], [3, 4, 5])

    def test_same_activity_twice_in_file(self):
        result = self.parse(strava_csv(strava_row(), strava_row()))
        self.assertEqual(len(result.activities), 1)
        self.assertEqual(len(result.skipped), 1)

    def test_sorted_oldest_first(self):
        result = self.parse(strava_csv(strava_row(activity_id="2", date="Mar 1, 2024, 1:00:00 AM"),
                                       strava_row(activity_id="1", date="Feb 1, 2024, 1:00:00 AM")))
        self.assertEqual([a["external_id"] for a in result.activities], ["1", "2"])

    def test_byte_order_mark_and_windows_encoding(self):
        bom = b"\xef\xbb\xbf" + strava_csv(strava_row())
        self.assertEqual(len(self.parse(bom).activities), 1)
        latin = strava_csv(strava_row(name="Café run"), encoding="cp1252")
        self.assertEqual(self.parse(latin).activities[0]["name"], "Café run")

    def test_wrong_files_give_a_clear_error(self):
        for data in (b"", b"   \n\n", strava_csv(header=["name", "type", "distance"]),
                     b"\x00\x01\x02 not a csv"):
            result = self.parse(data)
            self.assertFalse(result.ok, data)
            self.assertTrue(result.error)
            self.assertEqual(result.activities, ())

    def test_wrong_file_names_the_missing_column(self):
        result = self.parse(strava_csv(header=["Activity ID", "Activity Name"]))
        self.assertIn("Activity Date", result.error)

    def test_all_rows_bad_is_an_error(self):
        result = self.parse(strava_csv(strava_row(date="nope")))
        self.assertFalse(result.ok)
        self.assertIn("unreadable date", result.error)

    def test_huge_file_refused(self):
        with unittest.mock.patch("activity_import.MAX_IMPORT_BYTES", 10):
            self.assertFalse(self.parse(strava_csv(strava_row())).ok)

    def test_never_raises_on_odd_rows(self):
        short_row = ["5", "Jan 5, 2024, 1:00:00 AM", "Short", "Run", "", "600"]   # fewer cells than the header
        result = self.parse(strava_csv(short_row))
        self.assertTrue(result.ok)
        self.assertEqual(result.activities[0]["duration_min"], 10.0)


def imported(activity_id="strava-1", date="2024-01-06", **stats):
    return dict({"id": activity_id, "source": "strava", "date": date, "activity": "running",
                 "duration_min": 40.0}, **stats)


class ImportedStorageTest(unittest.TestCase):
    def test_imported_activity_validation(self):
        self.assertEqual(ImportedActivity.from_dict(imported()).day, datetime.date(2024, 1, 6))
        for bad in ({"source": "strava", "date": "2024-01-01"}, imported(date="06/01/2024"),
                    imported(activity_id=""), "text", dict(imported(), source=None)):
            with self.assertRaises(ValueError):
                ImportedActivity.from_dict(bad)

    def test_merge_skips_duplicates_and_keeps_feedback(self):
        log = ActivityLog()
        self.assertEqual(log.merge([ImportedActivity(imported())]), (1, 0))
        log.find("strava-1").set_feedback({"summary": "nice"})
        self.assertEqual(log.merge([ImportedActivity(imported()), ImportedActivity(imported("strava-2"))]), (1, 1))
        self.assertEqual(log.find("strava-1").to_dict()["coach_feedback"], {"summary": "nice"})

    def test_import_saves_and_reloads_from_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "data.json")
            data = DataManager(JsonFileStorage(path))
            data.load()
            self.assertEqual(data.import_activities([imported(), imported("strava-2", "2024-02-01")]),
                             (2, 0, True))
            again = DataManager(JsonFileStorage(path))
            self.assertEqual(again.load(), LoadStatus.OK)
            self.assertEqual(again.imported_count, 2)
            self.assertEqual(len(again.imported_in_month(2024, 2)), 1)
            self.assertEqual(again.import_activities([imported()]), (0, 1, True))

    def test_older_data_files_still_load(self):
        old = {"profile": None, "pending_session": None, "sessions": []}     # saved before imports existed
        data = DataManager(InMemoryStorage(old))
        self.assertEqual(data.load(), LoadStatus.OK)
        self.assertEqual(data.imported_count, 0)

    def test_one_damaged_imported_entry_does_not_lose_the_rest(self):
        payload = {"profile": None, "pending_session": None, "sessions": [],
                   "imported_activities": [imported(), {"id": 5}, "junk"]}
        data = DataManager(InMemoryStorage(payload))
        self.assertEqual(data.load(), LoadStatus.OK)
        self.assertEqual(data.imported_count, 1)

    def test_imported_activities_not_a_list_is_corrupt(self):
        data = DataManager(InMemoryStorage({"sessions": [], "imported_activities": {"a": 1}}))
        self.assertEqual(data.load(), LoadStatus.CORRUPT)

    def test_failed_save_keeps_nothing(self):
        data = DataManager(InMemoryStorage({"sessions": []}, succeed=False))
        data.load()
        self.assertEqual(data.import_activities([imported()]), (0, 0, False))
        self.assertEqual(data.imported_count, 0)

    def test_clear_imported_keeps_logged_sessions(self):
        storage = InMemoryStorage({"sessions": [{"id": 1, "date": "2024-01-01", "planned": {}, "actual": {}}],
                                   "imported_activities": [imported()]})
        data = DataManager(storage)
        data.load()
        self.assertTrue(data.clear_imported())
        self.assertEqual(data.imported_count, 0)
        self.assertEqual(len(data.sessions), 1)
        self.assertEqual(storage.load()[0]["imported_activities"], [])

    def test_reset_removes_imports(self):
        data = DataManager(InMemoryStorage({"sessions": [], "imported_activities": [imported()]}))
        data.load()
        self.assertTrue(data.reset())
        self.assertEqual(data.imported_count, 0)

    def test_find_record_by_kind(self):
        data = DataManager(InMemoryStorage({"sessions": [{"id": 1, "date": "2024-01-01", "planned": {},
                                                          "actual": {}}],
                                            "imported_activities": [imported()]}))
        data.load()
        self.assertEqual(data.find_record(1)[0], "session")
        self.assertEqual(data.find_record("strava-1")[0], "imported")
        self.assertEqual(data.find_record("strava-9"), (None, None))


if __name__ == "__main__":
    unittest.main()
