"""Offline tests for CoachService (the layer the GUI and console share) and the console controller.

A stub LLMClient stands in for Claude and InMemoryStorage stands in for the JSON file,
so no API key, network or disk is needed.
"""
import datetime
import io
import json
import unittest
from contextlib import redirect_stdout

import main
from ai_manager import AIManager, LLMClient
from coach_service import (
    CalendarEvent, CoachService, CompletedSession, PlannedSession, SuggestionResult,
)
from data_manager import DataManager, InMemoryStorage
from io_manager import InputManager, OutputManager
from logic_manager import LogicManager

TODAY = datetime.date(2026, 11, 18)
PROFILE = {
    'age': 30, 'sex': 'male', 'height_cm': 175, 'weight_kg': 70,
    'fitness_level': 'intermediate', 'health_concerns': 'none',
    'fitness_goal': 'run 5k', 'days_per_week': 4, 'session_duration_min': 90,
    'equipment': ['dumbbells', 'bench'],
    'preferred_activities': ['running', 'strength training'],
}
GOOD_AI = {
    'exercise_type': 'easy run', 'activity': 'running', 'duration_min': 30,
    'intensity': 'moderate', 'target_hr_bpm': 140,
    'target_pace_min_per_km': 6.5, 'equipment_used': ['bodyweight'],
    'warmup': '5 min walk', 'main_set': '20 min steady',
    'cooldown': '5 min walk', 'rationale': 'Builds aerobic base.',
    'risk_level': 'low',
}
LOG = {'duration_min': 32, 'avg_pace_min_per_km': 6.4, 'avg_hr_bpm': 142, 'rpe': 6,
       'pain_reported': False, 'comments': 'felt good'}


def reply(**changes):
    return json.dumps(dict(GOOD_AI, **changes)), None


class StubClient(LLMClient):
    def __init__(self, *replies):
        self._replies = list(replies)
        self.prompts = []

    def complete(self, system_prompt, user_prompt):
        self.prompts.append(user_prompt)
        return self._replies.pop(0)


def make_service(*replies, profile=PROFILE, sessions=None, pending=None):
    stub = StubClient(*replies)
    storage = InMemoryStorage({'profile': profile, 'pending_session': pending, 'sessions': sessions or []})
    data = DataManager(storage, clock=lambda: TODAY)
    service = CoachService(data, AIManager(stub), LogicManager(), clock=lambda: TODAY)
    service.load()
    return service, stub


class SuggestionTests(unittest.TestCase):
    def test_safe_suggestion_is_returned(self):
        service, stub = make_service(reply())
        result = service.suggest_session()
        self.assertIsInstance(result, SuggestionResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.decision['outcome'], 'accept')
        self.assertEqual(result.revisions, 0)

    def test_unsafe_draft_is_sent_back_with_reasons(self):
        service, stub = make_service(reply(target_hr_bpm=200), reply())
        seen = []
        result = service.suggest_session(on_revision=seen.append)
        self.assertTrue(result.ok)
        self.assertEqual(result.revisions, 1)
        self.assertTrue(any('heart rate' in reason for reason in seen[0]))
        self.assertIn('heart rate', stub.prompts[1])

    def test_gives_up_after_three_unsafe_drafts(self):
        service, _ = make_service(*[reply(risk_level='high')] * 3)
        result = service.suggest_session()
        self.assertFalse(result.ok)
        self.assertIn('could not produce a safe session', result.error)

    def test_api_failure_is_reported(self):
        service, _ = make_service((None, 'the AI service timed out'))
        result = service.suggest_session()
        self.assertEqual(result.error, 'coach unavailable: the AI service timed out')

    def test_rejection_reason_reaches_the_ai(self):
        service, stub = make_service(reply())
        service.suggest_session(user_feedback='too long, I only have 20 minutes')
        self.assertIn('too long, I only have 20 minutes', stub.prompts[0])

    def test_no_profile_means_no_ai_call(self):
        service, stub = make_service(profile=None)
        self.assertFalse(service.suggest_session().ok)
        self.assertEqual(stub.prompts, [])


class ScheduleAndLogTests(unittest.TestCase):
    def test_accept_schedules_the_session(self):
        service, _ = make_service()
        friday = TODAY + datetime.timedelta(days=2)
        self.assertTrue(service.accept_session(GOOD_AI, friday))
        self.assertEqual(service.pending_date(), friday)
        self.assertTrue(service.reschedule_pending(TODAY))
        self.assertEqual(service.pending_date(), TODAY)

    def test_pending_without_date_defaults_to_today(self):
        service, _ = make_service(pending=dict(GOOD_AI))
        self.assertEqual(service.pending_date(), TODAY)

    def test_log_moves_pending_into_history(self):
        service, _ = make_service(pending=dict(GOOD_AI))
        self.assertEqual(service.log_session(LOG), [])
        self.assertIsNone(service.pending_session)
        self.assertEqual(service.history()[0]['actual']['rpe'], 6)

    def test_invalid_log_is_refused(self):
        service, _ = make_service(pending=dict(GOOD_AI))
        errors = service.log_session(dict(LOG, avg_hr_bpm=215, rpe=11))
        self.assertTrue(any('rpe' in error for error in errors))
        self.assertIsNotNone(service.pending_session)
        self.assertTrue(service.log_session(dict(LOG, avg_hr_bpm=215)))   # far above max HR

    def test_nothing_to_log(self):
        service, _ = make_service()
        self.assertEqual(service.log_session(LOG), ['there is no planned session to log'])

    def test_discard(self):
        service, _ = make_service(pending=dict(GOOD_AI))
        self.assertTrue(service.discard_pending())
        self.assertIsNone(service.pending_session)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        done = {'id': 1, 'date': '2026-11-10', 'planned': GOOD_AI,
                'actual': dict(LOG, pain_reported=True)}
        old = {'id': 2, 'date': '2026-10-02', 'planned': GOOD_AI, 'actual': LOG}
        pending = dict(GOOD_AI, scheduled_for='2026-11-20')
        self.service, _ = make_service(sessions=[done, old], pending=pending)

    def test_month_contains_done_and_planned_sessions(self):
        events = self.service.calendar_events(2026, 11)
        self.assertEqual(sorted(events), [datetime.date(2026, 11, 10), datetime.date(2026, 11, 20)])
        done = events[datetime.date(2026, 11, 10)][0]
        planned = events[datetime.date(2026, 11, 20)][0]
        self.assertIsInstance(done, CompletedSession)
        self.assertIsInstance(planned, PlannedSession)
        self.assertEqual((done.status, planned.status), ('pain', 'planned'))

    def test_events_share_one_interface(self):
        for day_events in self.service.calendar_events(2026, 11).values():
            for event in day_events:
                self.assertIsInstance(event, CalendarEvent)
                self.assertTrue(event.summary())
                self.assertTrue(all(len(pair) == 2 for pair in event.details()))
        with self.assertRaises(TypeError):
            CalendarEvent(TODAY, {})

    def test_other_months_and_filters(self):
        self.assertEqual(list(self.service.calendar_events(2026, 10)), [datetime.date(2026, 10, 2)])
        self.assertEqual(list(self.service.calendar_events(2026, 11, pain_only=True)), [datetime.date(2026, 11, 10)])


class ProfileTests(unittest.TestCase):
    def test_valid_profile_is_saved(self):
        service, _ = make_service(profile=None)
        self.assertEqual(service.save_profile(dict(PROFILE, health_concerns='  ')), [])
        self.assertEqual(service.profile['health_concerns'], 'none')

    def test_out_of_range_and_missing_fields(self):
        service, _ = make_service()
        errors = service.validate_profile(dict(PROFILE, age=8, fitness_goal=' ', preferred_activities=[]))
        self.assertEqual(len(errors), 3)

    def test_activities_need_equipment(self):
        service, _ = make_service()
        profile = dict(PROFILE, preferred_activities=['swimming'], equipment=[])
        self.assertIn('none of your chosen activities can be done with your equipment',
                      service.validate_profile(profile))
        self.assertEqual(service.equipment_notes(profile), ['swimming needs swimming pool; it is skipped until you add it.'])

    def test_reset(self):
        service, _ = make_service()
        self.assertTrue(service.reset())
        self.assertFalse(service.has_profile)


class ScriptedInput(InputManager):
    def __init__(self, *answers):
        super().__init__(OutputManager())
        self._answers = list(answers)

    def _read(self, prompt):
        return self._answers.pop(0)


class ConsoleAppTests(unittest.TestCase):
    def test_suggest_accept_then_quit(self):
        service, _ = make_service(reply())
        console = main.ConsoleApp(service, OutputManager(), ScriptedInput('2', 'y', '6'))
        with redirect_stdout(io.StringIO()) as out:
            console.run()
        self.assertIn('SUGGESTED SESSION', out.getvalue())
        self.assertIsNotNone(service.pending_session)
        self.assertIn('Goodbye', out.getvalue())

    def test_menu_needs_a_profile_first(self):
        service, _ = make_service(profile=None)
        with redirect_stdout(io.StringIO()) as out:
            main.ConsoleApp(service, OutputManager(), ScriptedInput('4', '6')).run()
        self.assertIn('Please run initial setup first', out.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
