"""Cycling sessions are logged as average speed (km/h, 5-60) instead of pace (min/km).
Covers the shared rule, log validation, the plan-vs-actual review, calendar details and the CLI prompt."""
import datetime
import unittest

import io_manager
from ai_manager import AIManager, LLMClient
from coach_service import CoachService, CompletedSession, ImportedSession
from data_manager import DataManager, InMemoryStorage
from logic_manager import LogicManager, SessionReviewer, speed_from_pace, uses_speed

TODAY = datetime.date(2026, 11, 18)
PROFILE = {"name": "Alex", "age": 30, "sex": "male", "height_cm": 175, "weight_kg": 70,
           "fitness_level": "intermediate", "health_concerns": "none", "fitness_goal": "ride faster",
           "days_per_week": 4, "session_duration_min": 60, "equipment": ["bike"], "preferred_activities": ["cycling"]}
RIDE = {"exercise_type": "ftp threshold intervals", "activity": "cycling", "duration_min": 60, "intensity": "high",
        "target_hr_bpm": 160, "target_pace_min_per_km": None, "equipment_used": ["bike"], "warmup": "w",
        "main_set": "m", "cooldown": "c", "rationale": "r", "risk_level": "low", "scheduled_for": "2026-11-18"}
RIDE_LOG = {"duration_min": 60, "avg_pace_min_per_km": None, "avg_speed_kmh": 31.5, "avg_hr_bpm": 158,
            "rpe": 8, "pain_reported": False, "comments": ""}


class NoAI(LLMClient):
    def complete(self, system_prompt, user_prompt):
        return None, "not used"


def make_service(pending=RIDE):
    data = DataManager(InMemoryStorage({"profile": PROFILE, "pending_session": dict(pending) if pending else None,
                                        "sessions": []}), clock=lambda: TODAY)
    service = CoachService(data, AIManager(NoAI()), LogicManager(), clock=lambda: TODAY)
    service.load()
    return service


class SpeedRuleTest(unittest.TestCase):
    def test_which_activities_use_speed(self):
        self.assertTrue(uses_speed("cycling"))
        self.assertTrue(uses_speed(" Cycling "))
        for activity in ("running", "walking", "swimming", "", None):
            self.assertFalse(uses_speed(activity))

    def test_speed_from_pace(self):
        self.assertEqual(speed_from_pace(2.0), 30.0)
        self.assertEqual(speed_from_pace(6.0), 10.0)
        self.assertIsNone(speed_from_pace(None))
        self.assertIsNone(speed_from_pace(0))

    def test_service_helpers(self):
        service = make_service()
        self.assertTrue(service.logs_speed(RIDE))
        self.assertFalse(service.logs_speed({"activity": "running"}))
        self.assertFalse(service.logs_speed(None))
        self.assertEqual(service.suggested_speed(RIDE), 25)                                 # no target given
        self.assertEqual(service.suggested_speed(dict(RIDE, target_pace_min_per_km=2.0)), 30.0)
        self.assertEqual(service.suggested_speed(dict(RIDE, target_pace_min_per_km=0.5)), 60)   # clamped
        self.assertEqual(service.suggested_speed(dict(RIDE, target_speed_kmh=2)), 5)


class SpeedLogTest(unittest.TestCase):
    def test_ride_logged_with_speed(self):
        service = make_service()
        self.assertEqual(service.log_session(RIDE_LOG), [])
        actual = service.history()[0]["actual"]
        self.assertEqual(actual["avg_speed_kmh"], 31.5)
        self.assertIsNone(actual["avg_pace_min_per_km"])

    def test_speed_range_is_5_to_60(self):
        service = make_service()
        for speed in (4.9, 60.5, -1):
            errors = service.validate_log(dict(RIDE_LOG, avg_speed_kmh=speed))
            self.assertTrue(errors and "avg speed kmh" in errors[0], (speed, errors))
        for speed in (5, 60, None):
            self.assertEqual(service.validate_log(dict(RIDE_LOG, avg_speed_kmh=speed)), [], speed)

    def test_older_logs_without_speed_still_valid(self):
        service = make_service()
        log = {k: v for k, v in RIDE_LOG.items() if k != "avg_speed_kmh"}
        self.assertEqual(service.validate_log(log), [])

    def test_logic_validator_rejects_unrealistic_speed(self):
        errors = LogicManager().validate_session_log(dict(RIDE_LOG, avg_speed_kmh=80), PROFILE)
        self.assertTrue(any("km/h" in e for e in errors))


class SpeedReviewTest(unittest.TestCase):
    def review(self, planned, actual):
        return {c.metric: c for c in SessionReviewer().review(planned, actual, PROFILE).checks}

    def test_speed_shown_without_a_target(self):
        check = self.review(RIDE, RIDE_LOG)["Speed"]
        self.assertEqual((check.planned, check.actual, check.direction), (None, "31.5 km/h", None))
        self.assertNotIn("Pace", self.review(RIDE, RIDE_LOG))

    def test_target_pace_on_a_ride_is_compared_as_speed(self):
        planned = dict(RIDE, target_pace_min_per_km=2.0)          # 30 km/h
        self.assertEqual(self.review(planned, RIDE_LOG)["Speed"].direction, "on")      # 31.5 is within 5%
        fast = self.review(planned, dict(RIDE_LOG, avg_speed_kmh=35))["Speed"]
        self.assertEqual((fast.direction, fast.note), ("over", "5 km/h faster than target"))
        slow = self.review(planned, dict(RIDE_LOG, avg_speed_kmh=25))["Speed"]
        self.assertEqual(slow.direction, "under")

    def test_faster_ride_counts_as_harder(self):
        planned = dict(RIDE, intensity="moderate", target_speed_kmh=25, target_hr_bpm=158)
        result = SessionReviewer().review(planned, dict(RIDE_LOG, avg_speed_kmh=32, rpe=5), PROFILE)
        self.assertEqual(result.adherence, "harder_than_planned")


class SpeedDisplayTest(unittest.TestCase):
    def test_ride_details_show_speed(self):
        event = CompletedSession(TODAY, {"id": 1, "planned": RIDE, "actual": RIDE_LOG})
        rows = dict(event.details())
        self.assertEqual(rows["Speed"], "31.5 km/h")
        self.assertNotIn("Pace", rows)

    def test_ride_without_speed_says_dash(self):
        event = CompletedSession(TODAY, {"id": 1, "planned": RIDE, "actual": dict(RIDE_LOG, avg_speed_kmh=None)})
        self.assertEqual(dict(event.details())["Speed"], "-")

    def test_runs_still_show_pace(self):
        run = CompletedSession(TODAY, {"id": 1, "planned": {"activity": "running"},
                                       "actual": {"avg_pace_min_per_km": 6.5}})
        self.assertEqual(dict(run.details())["Pace"], "6:30 /km")

    def test_imported_ride_speed_reaches_the_review(self):
        ride = ImportedSession(TODAY, {"id": "strava-1", "source": "strava", "date": "2026-11-18",
                                       "activity": "cycling", "duration_min": 90, "avg_speed_kmh": 27.4})
        planned, actual = ride.comparison_inputs()
        self.assertEqual(actual["avg_speed_kmh"], 27.4)

    def test_console_history_line(self):
        out = io_manager.OutputManager()
        line = out.format_session_record({"id": 3, "date": "2026-11-18", "planned": RIDE, "actual": RIDE_LOG})
        self.assertIn("31.5 km/h", line)


class ConsoleSpeedPromptTest(unittest.TestCase):
    class Scripted(io_manager.InputManager):
        def __init__(self, answers):
            super().__init__(io_manager.OutputManager())
            self.answers, self.prompts = list(answers), []

        def _read(self, prompt):
            self.prompts.append(prompt)
            return self.answers.pop(0)

    def test_ride_asks_for_speed(self):
        inputs = self.Scripted(["60", "31.5", "158", "8", "n", ""])
        log = inputs.prompt_session_log(measure_speed=True)
        self.assertEqual((log["avg_speed_kmh"], log["avg_pace_min_per_km"]), (31.5, None))
        self.assertTrue(inputs.prompts[1].startswith("Average speed in km/h (5-60"))

    def test_run_asks_for_pace(self):
        inputs = self.Scripted(["30", "", "140", "4", "n", ""])
        log = inputs.prompt_session_log()
        self.assertEqual((log["avg_pace_min_per_km"], log["avg_speed_kmh"]), (None, None))
        self.assertTrue(inputs.prompts[1].startswith("Average pace in min/km"))


if __name__ == "__main__":
    unittest.main()
