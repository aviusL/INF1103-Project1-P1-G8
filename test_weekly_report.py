"""Tests for the Phase 2 new feature: the weekly progress report.

Covers every layer the feature touches (data, logic, AI, report wording, CoachService).
A stub LLMClient stands in for Claude, so no API key or network is needed. The report is
shown in the GUI's Progress tab; the console menu no longer has a report option.
"""
import datetime
import io
import json
import unittest
from contextlib import redirect_stdout

from ai_manager import (
    AIManager, LLMClient, NonEmptyTextField, PromptBuilder, ResponseSchema,
    SessionPromptBuilder, WeeklyNotePromptBuilder, WeeklyNoteRequest,
)
from data_manager import DataManager, InMemoryStorage, SessionRecord, WeekFilter
from coach_service import CoachService, WeeklyReportResult
from io_manager import InputManager, OutputManager, ReportFormatter
from logic_manager import (
    AdherenceInsight, EffortInsight, LogicManager, PainInsight, ProgressInsight,
    ProgressReport, VolumeInsight, WeeklyReporter, WeekSummary,
)

TODAY = datetime.date(2026, 11, 18)        # a Wednesday
MONDAY = datetime.date(2026, 11, 16)
LAST_MONDAY = datetime.date(2026, 11, 9)
PROFILE = {
    'age': 30, 'sex': 'female', 'fitness_level': 'intermediate', 'health_concerns': 'none',
    'fitness_goal': 'run 5k in 30 min', 'days_per_week': 3, 'session_duration_min': 60,
    'equipment': [], 'preferred_activities': ['running', 'cycling'],
}
NOTE = {'summary': 'Solid week: two runs done.', 'focus_next_week': 'Add one easy ride.'}


def session(day, minutes=30, rpe=5, hr=140, pain=False, intensity='moderate', activity='running', planned=None):
    return {
        'id': 1, 'date': day.isoformat(),
        'planned': {'exercise_type': 'easy run', 'activity': activity, 'intensity': intensity,
                    'duration_min': planned if planned is not None else minutes},
        'actual': {'duration_min': minutes, 'rpe': rpe, 'avg_hr_bpm': hr, 'pain_reported': pain, 'comments': ''},
    }


def week(sessions=0, planned=3, minutes=0, rpe=None, pain=0, start=MONDAY):
    return WeekSummary(start, sessions, planned, minutes, minutes, rpe, None, pain, {}, {})


class StubClient(LLMClient):
    """Returns scripted (text, error) replies and records every prompt."""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.prompts = []

    def complete(self, system_prompt, user_prompt):
        self.prompts.append(user_prompt)
        return self._replies.pop(0)


# --------------------------------------------------------------------------- #
# Data layer
# --------------------------------------------------------------------------- #
class WeekDataTests(unittest.TestCase):
    def make_data(self, sessions):
        data = DataManager(InMemoryStorage({'profile': PROFILE, 'pending_session': None, 'sessions': sessions}),
                           clock=lambda: TODAY)
        data.load()
        return data

    def test_week_start_is_monday(self):
        data = self.make_data([])
        self.assertEqual(data.week_start(0), MONDAY)
        self.assertEqual(data.week_start(1), LAST_MONDAY)

    def test_sessions_are_split_by_monday_to_sunday_week(self):
        days = ['2026-11-08', '2026-11-09', '2026-11-15', '2026-11-16', '2026-11-18', 'not a date']
        data = self.make_data([dict(session(TODAY), id=n, date=d) for n, d in enumerate(days, start=1)])
        self.assertEqual([s['id'] for s in data.sessions_in_week(0)], [4, 5])
        self.assertEqual([s['id'] for s in data.sessions_in_week(1)], [2, 3])

    def test_week_filter_skips_records_without_a_valid_date(self):
        week_filter = WeekFilter(TODAY)
        self.assertTrue(week_filter.matches(SessionRecord(1, '2026-11-20', {}, {})))
        self.assertFalse(week_filter.matches(SessionRecord(2, None, {}, {})))
        self.assertFalse(week_filter.matches(SessionRecord(3, '18/11/2026', {}, {})))


# --------------------------------------------------------------------------- #
# Logic layer
# --------------------------------------------------------------------------- #
class WeekSummaryTests(unittest.TestCase):
    def test_totals_and_averages(self):
        summary = WeekSummary.from_sessions(MONDAY, [
            session(MONDAY, minutes=30, rpe=5, hr=140, intensity='moderate', planned=40),
            session(TODAY, minutes=45, rpe=8, hr=160, pain=True, intensity='low', activity='cycling'),
        ], planned_sessions=3)
        self.assertEqual((summary.sessions, summary.planned_sessions, summary.adherence_pct), (2, 3, 67))
        self.assertEqual((summary.minutes, summary.planned_minutes), (75, 85))
        self.assertEqual((summary.avg_rpe, summary.avg_hr, summary.pain_sessions), (6.5, 150.0, 1))
        self.assertEqual(summary.intensity_counts, {'moderate': 1, 'low': 1})
        self.assertEqual(summary.activity_counts, {'running': 1, 'cycling': 1})

    def test_empty_week(self):
        summary = WeekSummary.from_sessions(MONDAY, [], planned_sessions=3)
        self.assertEqual((summary.sessions, summary.minutes, summary.adherence_pct), (0, 0, 0))
        self.assertIsNone(summary.avg_rpe)
        self.assertIsNone(summary.avg_hr)

    def test_bad_or_missing_values_are_ignored(self):
        odd = {'id': 9, 'date': '2026-11-17', 'planned': None,
               'actual': {'duration_min': True, 'rpe': 'hard', 'avg_hr_bpm': None, 'pain_reported': 'yes'}}
        summary = WeekSummary.from_sessions(MONDAY, [odd, session(TODAY, rpe=6)], planned_sessions=3)
        self.assertEqual((summary.sessions, summary.minutes, summary.avg_rpe, summary.pain_sessions), (2, 30, 6.0, 0))

    def test_to_dict_is_json_ready(self):
        summary = WeekSummary.from_sessions(MONDAY, [session(TODAY)], planned_sessions=3)
        payload = json.loads(json.dumps(summary.to_dict()))
        self.assertEqual(payload['week_start'], '2026-11-16')
        self.assertEqual(payload['sessions_done'], 1)


class InsightTests(unittest.TestCase):
    def test_adherence(self):
        self.assertIn('No sessions logged', AdherenceInsight().describe(week(0), week())[0])
        self.assertIn('1 of 3 planned sessions done, 2 to go', AdherenceInsight().describe(week(1), week())[0])
        self.assertIn('Weekly target reached', AdherenceInsight().describe(week(3), week())[0])

    def test_volume_jump_over_30_percent_is_flagged(self):
        self.assertIn('up 50%', VolumeInsight().describe(week(minutes=150), week(minutes=100))[0])
        self.assertEqual(VolumeInsight().describe(week(minutes=125), week(minutes=100)), [])
        self.assertEqual(VolumeInsight().describe(week(minutes=90), week(minutes=0)), [])

    def test_effort(self):
        self.assertIn('Average effort is high', EffortInsight().describe(week(rpe=8.5), week(rpe=8.0))[0])
        self.assertIn('rose from RPE 4.5', EffortInsight().describe(week(rpe=6.5), week(rpe=4.5))[0])
        self.assertEqual(EffortInsight().describe(week(rpe=5.0), week(rpe=4.5)), [])
        self.assertEqual(EffortInsight().describe(week(rpe=None), week(rpe=4.5)), [])

    def test_pain(self):
        self.assertEqual(PainInsight().describe(week(pain=0), week()), [])
        self.assertIn('1 session this week', PainInsight().describe(week(pain=1), week())[0])
        self.assertIn('2 sessions this week', PainInsight().describe(week(pain=2), week())[0])

    def test_insight_interface_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            ProgressInsight()


class WeeklyReporterTests(unittest.TestCase):
    def test_report_compares_this_week_with_last_week(self):
        report = WeeklyReporter().build(PROFILE, [session(TODAY, minutes=60)], [session(LAST_MONDAY, minutes=30)], MONDAY)
        self.assertIsInstance(report, ProgressReport)
        self.assertEqual((report.this_week.week_start, report.last_week.week_start), (MONDAY, LAST_MONDAY))
        self.assertEqual(report.this_week.planned_sessions, 3)          # from profile days_per_week
        self.assertTrue(any('up 100%' in text for text in report.insights))
        self.assertTrue(report.has_sessions)
        self.assertEqual(json.loads(json.dumps(report.to_dict()))['this_week']['minutes_done'], 60)

    def test_new_insight_needs_no_change_to_reporter(self):
        class RestDayInsight(ProgressInsight):
            def describe(self, current, previous):
                return ['Take a rest day.'] if current.sessions >= 1 else []

        report = WeeklyReporter([RestDayInsight()]).build(PROFILE, [session(TODAY)], [], MONDAY)
        self.assertEqual(report.insights, ('Take a rest day.',))

    def test_rejects_objects_that_are_not_insights(self):
        with self.assertRaises(TypeError):
            WeeklyReporter([object()])

    def test_empty_report_has_no_sessions(self):
        self.assertFalse(WeeklyReporter().build(PROFILE, [], [], MONDAY).has_sessions)

    def test_logic_manager_facade_does_not_modify_inputs(self):
        this_week, last_week = [session(TODAY)], [session(LAST_MONDAY)]
        before = json.dumps([this_week, last_week, PROFILE])
        report = LogicManager().weekly_progress_report(PROFILE, this_week, last_week, MONDAY)
        self.assertEqual(report.this_week.sessions, 1)
        self.assertEqual(json.dumps([this_week, last_week, PROFILE]), before)


# --------------------------------------------------------------------------- #
# AI layer
# --------------------------------------------------------------------------- #
class WeeklyNoteAITests(unittest.TestCase):
    def setUp(self):
        self.report = WeeklyReporter().build(PROFILE, [session(TODAY)], [], MONDAY).to_dict()

    def test_prompt_contains_report_and_only_relevant_profile_fields(self):
        prompt = WeeklyNotePromptBuilder().build(WeeklyNoteRequest(PROFILE, self.report))
        self.assertTrue(prompt.startswith('Write the weekly coaching note for this user:'))
        payload = json.loads(prompt.split('\n', 1)[1])
        self.assertEqual(payload['weekly_report']['this_week']['sessions_done'], 1)
        self.assertEqual(payload['profile']['fitness_goal'], 'run 5k in 30 min')
        self.assertNotIn('age', payload['profile'])

    def test_both_prompt_builders_share_the_base_class(self):
        for builder in (SessionPromptBuilder(), WeeklyNotePromptBuilder()):
            self.assertIsInstance(builder, PromptBuilder)
            self.assertIn('Reply with ONLY one JSON object', builder.system_prompt)
            self.assertIn('previous reply was rejected: oops', builder.build_retry('base', 'oops'))
        with self.assertRaises(TypeError):
            PromptBuilder()

    def test_note_schema(self):
        schema = ResponseSchema.for_weekly_note()
        self.assertIsNone(schema.validate(NOTE))
        self.assertEqual(schema.validate({'summary': 'ok'}), 'missing field: focus_next_week')
        self.assertEqual(schema.validate(dict(NOTE, summary='   ')), 'summary must not be empty')
        self.assertEqual(NonEmptyTextField('x').check_value('fine'), None)

    def test_request_weekly_note_success(self):
        stub = StubClient((json.dumps(NOTE), None))
        result = AIManager(stub).request_weekly_note(PROFILE, self.report)
        self.assertTrue(result.ok)
        self.assertEqual(result.data, NOTE)

    def test_malformed_note_is_retried_with_the_reason(self):
        stub = StubClient((json.dumps({'summary': ''}), None), (json.dumps(NOTE), None))
        with self.assertLogs('ai_manager', level='WARNING'):
            result = AIManager(stub).request_weekly_note(PROFILE, self.report)
        self.assertTrue(result.ok)
        self.assertIn('previous reply was rejected: missing field: focus_next_week', stub.prompts[1])

    def test_api_error_is_returned(self):
        result = AIManager(StubClient((None, 'the AI service timed out'))).request_weekly_note(PROFILE, self.report)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, 'the AI service timed out')


# --------------------------------------------------------------------------- #
# IO layer
# --------------------------------------------------------------------------- #
class ScriptedInput(InputManager):
    def __init__(self, *answers):
        super().__init__(OutputManager())
        self._answers = list(answers)

    def _read(self, prompt):
        return self._answers.pop(0)


class ReportFormatterTests(unittest.TestCase):
    def setUp(self):
        self.report = WeeklyReporter().build(
            PROFILE, [session(MONDAY, minutes=40, rpe=6, pain=True), session(TODAY, minutes=35, rpe=7)],
            [session(LAST_MONDAY, minutes=30, rpe=5)], MONDAY)

    def test_console_menu_has_no_report_option_and_6_quits(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ScriptedInput('6').prompt_main_menu(True, False), 'quit')
            self.assertEqual(ScriptedInput('7', '5').prompt_main_menu(True, False), 'reset')
        self.assertNotIn('Weekly progress report', out.getvalue())
        self.assertIn('6) Quit', out.getvalue())
        self.assertIn('Please enter a number from 1 to 6.', out.getvalue())

    def test_report_table_rows(self):
        rows = dict((label, (now, before)) for label, now, before in ReportFormatter.rows(self.report))
        self.assertEqual(ReportFormatter.heading(self.report), 'Week of 16 Nov 2026 (compared with 9 Nov 2026)')
        self.assertEqual(rows['Sessions'], ('2 of 3 (67%)', '1 of 3 (33%)'))
        self.assertEqual(rows['Minutes'][0], '75 of 75 planned')
        self.assertEqual(rows['Avg effort'], ('RPE 6.5', 'RPE 5'))
        self.assertEqual(rows['Pain reported'], ('1', 'none'))
        self.assertIn('2 of 3 planned sessions done', ' '.join(self.report.insights))

    def test_mix_and_amount(self):
        self.assertEqual(ReportFormatter.mix({'moderate': 2}), 'moderate 2')
        self.assertEqual(ReportFormatter.mix({}), '-')
        self.assertEqual(ReportFormatter.amount(30.0), '30')


# --------------------------------------------------------------------------- #
# CoachService: the whole feature end to end
# --------------------------------------------------------------------------- #
class ServiceWeeklyReportTests(unittest.TestCase):
    def run_report(self, sessions, *replies, with_note=True):
        stub = StubClient(*replies)
        data = DataManager(InMemoryStorage({'profile': PROFILE, 'pending_session': None, 'sessions': sessions}),
                           clock=lambda: TODAY)
        service = CoachService(data, AIManager(stub), LogicManager(), clock=lambda: TODAY)
        service.load()
        return service.weekly_report(with_coach_note=with_note), stub

    def test_report_with_coach_note(self):
        result, stub = self.run_report([session(TODAY)], (json.dumps(NOTE), None))
        self.assertIsInstance(result, WeeklyReportResult)
        self.assertEqual(len(stub.prompts), 1)
        self.assertEqual(result.coach_note, NOTE)
        self.assertEqual(result.report.this_week.sessions, 1)

    def test_ai_failure_still_returns_the_report(self):
        result, _ = self.run_report([session(TODAY)], (None, 'could not connect to the AI service'))
        self.assertEqual(ReportFormatter.rows(result.report)[0][1], '1 of 3 (33%)')
        self.assertIsNone(result.coach_note)
        self.assertEqual(result.note_error, 'could not connect to the AI service')

    def test_no_sessions_means_no_ai_call(self):
        result, stub = self.run_report([])
        self.assertEqual(stub.prompts, [])
        self.assertIn('No sessions logged this week yet', result.report.insights[0])

    def test_note_can_be_skipped(self):
        result, stub = self.run_report([session(TODAY)], with_note=False)
        self.assertEqual(stub.prompts, [])
        self.assertIsNone(result.note_error)


if __name__ == '__main__':
    unittest.main(verbosity=2)
