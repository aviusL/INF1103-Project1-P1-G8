"""Tests for coach feedback on one session: the logic layer's plan-vs-actual review, the AI request
and schema, and the CoachService workflow that saves the feedback. Uses a stub LLM, no network."""
import datetime
import json
import unittest

from ai_manager import AIManager, LLMClient, ResponseSchema, SessionFeedbackPromptBuilder, SessionFeedbackRequest
from coach_service import CalendarEvent, CoachService, CompletedSession, ImportedSession, PlannedSession
from data_manager import DataManager, InMemoryStorage
from logic_manager import (LogicManager, ReviewCheck, ReviewCheckRule, SessionReviewer, pace_text)

TODAY = datetime.date(2026, 11, 18)
PROFILE = {"name": "Alex", "age": 30, "sex": "male", "height_cm": 175, "weight_kg": 70,
           "fitness_level": "intermediate", "health_concerns": "none", "fitness_goal": "sub-50 10k",
           "days_per_week": 4, "session_duration_min": 45, "equipment": [], "preferred_activities": ["running"]}
PLAN = {"exercise_type": "easy run", "activity": "running", "duration_min": 30, "intensity": "low",
        "target_hr_bpm": 135, "target_pace_min_per_km": 6.5, "equipment_used": ["bodyweight"], "warmup": "w",
        "main_set": "m", "cooldown": "c", "rationale": "r", "risk_level": "low"}
ON_PLAN = {"duration_min": 31, "avg_pace_min_per_km": 6.45, "avg_hr_bpm": 133, "rpe": 3, "pain_reported": False}
FEEDBACK = {"summary": "Right on plan.", "went_well": ["Even pacing", "  "], "to_improve": [],
            "next_time": "Repeat it next week."}
IMPORTED = {"id": "strava-1", "source": "strava", "date": "2026-11-04", "name": "Night tempo",
            "activity": "running", "sport_type": "Run", "duration_min": 52.0, "distance_km": 10.02,
            "avg_pace_min_per_km": 5.19, "avg_hr_bpm": 152, "perceived_exertion": 7}


class Stub(LLMClient):
    def __init__(self, reply=FEEDBACK, error=None):
        self.reply, self.error, self.prompts = reply, error, []

    def complete(self, system_prompt, user_prompt):
        self.prompts.append((system_prompt, user_prompt))
        if self.error:
            return None, self.error
        return (self.reply if isinstance(self.reply, str) else json.dumps(self.reply)), None

    def payload(self, index=-1):
        return json.loads(self.prompts[index][1].split("\n", 1)[1])


def review(planned, actual, profile=PROFILE):
    return SessionReviewer().review(planned, actual, profile)


class SessionReviewerTest(unittest.TestCase):
    def test_on_plan(self):
        result = review(PLAN, ON_PLAN)
        self.assertEqual(result.adherence, "on_plan")
        self.assertEqual({c.metric: c.direction for c in result.checks},
                         {"Duration": "on", "Heart rate": "on", "Pace": "on", "Effort": "on"})
        self.assertEqual(result.flags, ())

    def test_harder_than_planned(self):
        result = review(PLAN, dict(ON_PLAN, avg_hr_bpm=160, rpe=7, avg_pace_min_per_km=5.5))
        self.assertEqual(result.adherence, "harder_than_planned")
        pace = next(c for c in result.checks if c.metric == "Pace")
        self.assertEqual(pace.direction, "over")            # faster pace = harder
        self.assertIn("60 s/km faster", pace.note)

    def test_easier_than_planned(self):
        result = review(dict(PLAN, intensity="high", target_hr_bpm=170), dict(ON_PLAN, avg_hr_bpm=140, rpe=4))
        self.assertEqual(result.adherence, "easier_than_planned")

    def test_mixed(self):
        result = review(PLAN, dict(ON_PLAN, avg_hr_bpm=150, rpe=1, avg_pace_min_per_km=8.0))
        self.assertEqual(result.adherence, "mixed")

    def test_longer_and_shorter(self):
        self.assertEqual(review(PLAN, dict(ON_PLAN, duration_min=40)).adherence, "longer_than_planned")
        self.assertEqual(review(PLAN, dict(ON_PLAN, duration_min=20)).adherence, "shorter_than_planned")
        note = next(c for c in review(PLAN, dict(ON_PLAN, duration_min=40)).checks if c.metric == "Duration").note
        self.assertEqual(note, "33% longer than planned")

    def test_intensity_beats_duration(self):
        self.assertEqual(review(PLAN, dict(ON_PLAN, duration_min=20, rpe=8)).adherence, "harder_than_planned")

    def test_safety_flags(self):
        flags = review(PLAN, dict(ON_PLAN, pain_reported=True, avg_hr_bpm=180, rpe=9)).flags
        self.assertEqual(len(flags), 3)
        self.assertIn("Pain", flags[0])
        self.assertIn("90%", flags[1])

    def test_no_plan_for_imported_history(self):
        result = review(None, {"duration_min": 52, "avg_hr_bpm": 152, "avg_pace_min_per_km": 5.19, "rpe": None})
        self.assertEqual(result.adherence, "no_plan")
        self.assertTrue(all(c.direction is None for c in result.checks))
        hr = next(c for c in result.checks if c.metric == "Heart rate")
        self.assertEqual(hr.note, "80% of your estimated max HR")

    def test_missing_numbers_are_skipped(self):
        result = review(PLAN, {"duration_min": "?", "rpe": True})
        self.assertEqual(result.checks, ())
        self.assertEqual(result.adherence, "on_plan")

    def test_no_age_means_no_max_hr(self):
        result = review(None, {"avg_hr_bpm": 150}, profile={})
        self.assertEqual(result.checks[0].note, "average heart rate")

    def test_custom_checks_plug_in(self):
        class CadenceCheck(ReviewCheckRule):
            def check(self, planned, actual, max_hr):
                return ReviewCheck("Cadence", None, "180 spm", None, "steps per minute")
        result = SessionReviewer([CadenceCheck()]).review(PLAN, ON_PLAN, PROFILE)
        self.assertEqual([c.metric for c in result.checks], ["Cadence"])
        with self.assertRaises(TypeError):
            SessionReviewer(["not a rule"])

    def test_to_dict_and_pace_text(self):
        data = review(PLAN, ON_PLAN).to_dict()
        self.assertEqual(data["adherence_label"], "On plan")
        self.assertEqual(data["checks"][0]["metric"], "Duration")
        self.assertEqual(pace_text(6.5), "6:30 /km")
        self.assertEqual(pace_text(5.999), "6:00 /km")


class FeedbackAITest(unittest.TestCase):
    def test_schema(self):
        schema = ResponseSchema.for_session_feedback()
        self.assertIsNone(schema.validate(FEEDBACK))
        self.assertIn("summary", schema.validate(dict(FEEDBACK, summary="  ")))
        self.assertIn("went_well", schema.validate(dict(FEEDBACK, went_well="good")))
        self.assertIn("next_time", schema.validate({k: v for k, v in FEEDBACK.items() if k != "next_time"}))

    def test_request_payload_keeps_only_coaching_profile_fields(self):
        payload = SessionFeedbackRequest(PROFILE, {"date": "x"}, {"adherence": "on_plan"}).to_payload()
        self.assertNotIn("weight_kg", payload["profile"])
        self.assertEqual(payload["profile"]["fitness_goal"], "sub-50 10k")
        self.assertEqual(payload["app_review"], {"adherence": "on_plan"})

    def test_prompt_asks_for_plan_comparison_and_json(self):
        prompts = SessionFeedbackPromptBuilder()
        self.assertIn("planned", prompts.system_prompt)
        self.assertIn("ONLY one JSON object", prompts.system_prompt)

    def test_malformed_reply_is_retried_then_reported(self):
        stub = Stub(reply="not json")
        result = AIManager(stub).request_session_feedback(PROFILE, {}, {})
        self.assertFalse(result.ok)
        self.assertIn("malformed", result.error)
        self.assertEqual(len(stub.prompts), 2)

    def test_api_failure_passes_through(self):
        result = AIManager(Stub(error="the AI service timed out")).request_session_feedback(PROFILE, {}, {})
        self.assertEqual(result.error, "the AI service timed out")


def make_service(stub=None, profile=PROFILE, storage=None):
    storage = storage or InMemoryStorage({
        "profile": profile, "pending_session": dict(PLAN, scheduled_for="2026-11-20"),
        "sessions": [{"id": 1, "date": "2026-11-16", "planned": PLAN, "actual": ON_PLAN}],
        "imported_activities": [IMPORTED],
    })
    data = DataManager(storage, clock=lambda: TODAY)
    service = CoachService(data, AIManager(stub or Stub()), LogicManager(), clock=lambda: TODAY)
    service.load()
    return service, storage


class CalendarEventTest(unittest.TestCase):
    def setUp(self):
        self.service, _ = make_service()
        self.events = self.service.calendar_events(2026, 11)

    def test_all_three_kinds_on_the_calendar(self):
        kinds = {type(e) for day in self.events.values() for e in day}
        self.assertEqual(kinds, {CompletedSession, ImportedSession, PlannedSession})

    def test_shared_interface(self):
        for event in (e for day in self.events.values() for e in day):
            self.assertIsInstance(event, CalendarEvent)
            self.assertIsInstance(event.summary(), str)
            self.assertTrue(event.details())

    def test_imported_event(self):
        [event] = self.events[datetime.date(2026, 11, 4)]
        self.assertEqual((event.title, event.status, event.minutes, event.effort), ("Night tempo", "imported", 52.0, 7))
        self.assertEqual(event.summary(), "10.02 km · 52 min · 5:11 /km · 152 bpm")
        self.assertIn(("Distance", "10.02 km"), event.details())
        self.assertTrue(event.can_review)
        self.assertNotIn("Calories", [name for name, _ in event.details()])     # blank stats are left out

    def test_only_done_sessions_can_be_reviewed(self):
        planned = self.events[datetime.date(2026, 11, 20)][0]
        self.assertFalse(planned.can_review)
        self.assertIsNone(planned.minutes)

    def test_filters(self):
        self.assertNotIn(datetime.date(2026, 11, 4), self.service.calendar_events(2026, 11, pain_only=True))
        self.assertIn(datetime.date(2026, 11, 4), self.service.calendar_events(2026, 11, min_rpe=7))
        self.assertNotIn(datetime.date(2026, 11, 4), self.service.calendar_events(2026, 11, min_rpe=8))

    def test_other_months_and_overview(self):
        self.assertEqual(self.service.calendar_events(2026, 10), {})
        self.assertEqual(self.service.imported_overview(),
                         (1, datetime.date(2026, 11, 4), datetime.date(2026, 11, 4)))


class SessionFeedbackServiceTest(unittest.TestCase):
    def test_feedback_on_a_logged_session_compares_plan_and_actual(self):
        stub = Stub()
        service, storage = make_service(stub)
        result = service.session_feedback(1)
        self.assertTrue(result.ok and result.saved)
        self.assertEqual(result.feedback["went_well"], ["Even pacing"])        # blank points dropped
        self.assertEqual(result.feedback["adherence"], "on_plan")               # the code's verdict
        self.assertEqual(result.feedback["generated_on"], "2026-11-18")
        sent = stub.payload()
        self.assertEqual(sent["session"]["planned"]["duration_min"], 30)
        self.assertEqual(sent["session"]["actual"]["duration_min"], 31)
        self.assertEqual(sent["app_review"]["adherence"], "on_plan")
        self.assertNotIn("warmup", sent["session"]["planned"])
        stored = storage.load()[0]["sessions"][0]
        self.assertEqual(stored["coach_feedback"]["summary"], "Right on plan.")
        self.assertEqual(service.event_for(1).feedback["summary"], "Right on plan.")

    def test_feedback_on_imported_history(self):
        stub = Stub()
        service, storage = make_service(stub)
        result = service.session_feedback("strava-1")
        self.assertTrue(result.ok)
        sent = stub.payload()
        self.assertEqual(sent["session"]["source"], "strava")
        self.assertNotIn("planned", sent["session"])
        self.assertEqual(sent["app_review"]["adherence"], "no_plan")
        self.assertIn("coach_feedback", storage.load()[0]["imported_activities"][0])

    def test_ai_failure_keeps_the_review_and_saves_nothing(self):
        service, storage = make_service(Stub(error="could not connect to the AI service"))
        result = service.session_feedback(1)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "coach unavailable: could not connect to the AI service")
        self.assertEqual(result.review.adherence, "on_plan")
        self.assertNotIn("coach_feedback", storage.load()[0]["sessions"][0])

    def test_unknown_session_and_missing_profile(self):
        service, _ = make_service()
        self.assertIn("could not be found", service.session_feedback(99).error)
        no_profile, _ = make_service(profile=None)
        self.assertIn("profile", no_profile.session_feedback(1).error)

    def test_save_failure_still_returns_the_feedback(self):
        storage = InMemoryStorage({"profile": PROFILE, "pending_session": None,
                                   "sessions": [{"id": 1, "date": "2026-11-16", "planned": PLAN,
                                                 "actual": ON_PLAN}]}, succeed=False)
        service, _ = make_service(storage=storage)
        result = service.session_feedback(1)
        self.assertTrue(result.ok)
        self.assertFalse(result.saved)
        self.assertIsNone(service.event_for(1).feedback)                    # rolled back in memory too

    def test_regenerating_replaces_the_old_feedback(self):
        stub = Stub()
        service, _ = make_service(stub)
        service.session_feedback(1)
        stub.reply = dict(FEEDBACK, summary="Second opinion.")
        service.session_feedback(1)
        self.assertEqual(service.event_for(1).feedback["summary"], "Second opinion.")


class ImportServiceTest(unittest.TestCase):
    def test_import_uses_the_users_time_zone(self):
        from settings_manager import AppSettings, SettingsManager
        from test_activity_import import strava_csv, strava_row
        settings = SettingsManager.in_memory()
        settings.save(AppSettings(timezone="Asia/Singapore"))
        data = DataManager(InMemoryStorage(), clock=lambda: TODAY)
        service = CoachService(data, AIManager(Stub()), LogicManager(), clock=lambda: TODAY, settings=settings)
        service.load()
        summary = service.import_history(strava_csv(strava_row(date="Nov 3, 2026, 11:30:00 PM")))
        self.assertTrue(summary.ok)
        self.assertEqual((summary.added, summary.first_day), (1, datetime.date(2026, 11, 4)))
        self.assertIn("Imported 1 activity", summary.message())
        again = service.import_history(strava_csv(strava_row(date="Nov 3, 2026, 11:30:00 PM")))
        self.assertEqual(again.message(), "No new activities, 1 already imported.")

    def test_unknown_source_and_bad_file(self):
        service, _ = make_service()
        self.assertIn("not supported", service.import_history(b"x", source="garmin").error)
        self.assertTrue(service.import_history(b"").message().startswith("Import failed:"))

    def test_disk_failure_reported(self):
        from test_activity_import import strava_csv, strava_row
        storage = InMemoryStorage({"sessions": []}, succeed=False)
        service, _ = make_service(storage=storage)
        summary = service.import_history(strava_csv(strava_row()))
        self.assertIn("could not save", summary.error)
        self.assertEqual(service.imported_count, 0)

    def test_clear_imported(self):
        service, _ = make_service()
        self.assertTrue(service.clear_imported())
        self.assertEqual(service.imported_count, 0)
        self.assertEqual(len(service.history()), 1)


if __name__ == "__main__":
    unittest.main()
