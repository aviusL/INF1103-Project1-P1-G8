import copy
import datetime
import json
import os
import tempfile
import unittest

import ai_manager
import data_manager

from logic_manager import (
    LogicManager, ProfilePreferences, WeeklyPlanner, SessionEvaluator,
    EvaluationContext, FitnessRule, HardRule, SoftRule, HeartRateRule,
    SessionLogValidator,
)

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


def session(**changes):
    result = copy.deepcopy(GOOD_AI)
    result.update(changes)
    return result


def past(intensity='moderate', duration=30, rpe=5, pain=False):
    return {'id': 1, 'date': '2026-10-01', 'planned': {'intensity': intensity},
            'actual': {'duration_min': duration, 'rpe': rpe, 'pain_reported': pain}}


class LogicManagerTests(unittest.TestCase):
    def setUp(self):
        self.logic = LogicManager()

    def result(self, suggested=None, profile=None, history=None, target=None):
        return self.logic.evaluate_session(suggested or session(), profile or PROFILE,
                                           history if history is not None else [], target)

    def test_good_session_accepted(self):
        self.assertEqual(self.result()['outcome'], 'accept')
        self.assertEqual(self.result()['score'], 100)

    def test_rejects_high_hr(self):
        self.assertEqual(self.result(session(target_hr_bpm=180))['outcome'], 'reject')

    def test_rejects_bad_duration(self):
        self.assertEqual(self.result(session(duration_min=200))['outcome'], 'reject')

    def test_rejects_bad_pace(self):
        self.assertEqual(self.result(session(target_pace_min_per_km=1.5))['outcome'], 'reject')

    def test_allows_null_pace(self):
        self.assertEqual(self.result(session(target_pace_min_per_km=None))['outcome'], 'accept')

    def test_rejects_high_ai_risk(self):
        self.assertEqual(self.result(session(risk_level='high'))['outcome'], 'reject')

    def test_rejects_high_intensity_with_health_concerns(self):
        profile = dict(PROFILE, health_concerns='knee injury')
        self.assertEqual(self.result(session(intensity='high'), profile)['outcome'], 'reject')

    def test_rejects_beginner_long_session(self):
        profile = dict(PROFILE, fitness_level='beginner')
        self.assertEqual(self.result(session(duration_min=90), profile)['outcome'], 'reject')

    def test_rejects_beginner_high_intensity(self):
        profile = dict(PROFILE, fitness_level='beginner')
        self.assertEqual(self.result(session(intensity='high'), profile)['outcome'], 'reject')

    def test_rejects_high_intensity_after_pain_or_rpe(self):
        for previous in [past(pain=True), past(rpe=9)]:
            with self.subTest(previous=previous):
                self.assertEqual(self.result(session(intensity='high'), history=[previous])['outcome'], 'reject')

    def test_rejects_consecutive_high_sessions(self):
        self.assertEqual(self.result(session(intensity='high'), history=[past(intensity='high')])['outcome'], 'reject')

    def test_rejects_unchosen_activity(self):
        self.assertEqual(self.result(session(activity='swimming'))['outcome'], 'reject')

    def test_swimming_requires_pool(self):
        profile = dict(PROFILE, preferred_activities=['swimming'])
        self.assertEqual(self.result(session(activity='swimming'), profile)['outcome'], 'reject')
        profile['equipment'] = ['swimming pool']
        suggested = session(activity='swimming', equipment_used=['swimming pool'], target_pace_min_per_km=None)
        self.assertEqual(self.result(suggested, profile)['outcome'], 'accept')

    def test_rejects_missing_equipment(self):
        self.assertEqual(self.result(session(equipment_used=['barbell']))['outcome'], 'reject')
        self.assertEqual(self.result(session(equipment_used=['Dumbbells', 'bench']))['outcome'], 'accept')

    def test_bodyweight_always_available(self):
        self.assertEqual(self.result(profile=dict(PROFILE, equipment=[]))['outcome'], 'accept')

    def test_rejects_over_available_time(self):
        self.assertEqual(self.result(session(duration_min=100))['outcome'], 'reject')
        self.assertEqual(self.result(session(duration_min=90))['outcome'], 'accept')

    def test_flags_duration_progression(self):
        result = self.result(session(duration_min=50), history=[past(duration=30)])
        self.assertEqual(result['outcome'], 'flag')
        self.assertEqual(result['score'], 80)

    def test_flags_medium_risk(self):
        self.assertEqual(self.result(session(risk_level='medium'))['outcome'], 'flag')

    def test_flags_low_heart_rate(self):
        self.assertEqual(self.result(session(target_hr_bpm=70))['outcome'], 'flag')

    def test_rejection_takes_priority_over_flags(self):
        result = self.result(session(risk_level='high', target_hr_bpm=70))
        self.assertEqual(result['outcome'], 'reject')
        self.assertEqual(result['score'], 0)

    def test_preferences_and_older_profile_defaults(self):
        old = dict(PROFILE)
        for field in ('preferred_activities', 'days_per_week', 'session_duration_min'):
            old.pop(field)
        pref = ProfilePreferences(old)
        self.assertIsNone(pref.usable_activities())
        self.assertEqual(pref.available_days(), 3)
        self.assertEqual(pref.available_minutes(), 120)
        self.assertEqual(self.result(profile=old)['outcome'], 'accept')

    def test_usable_activities_and_missing_equipment(self):
        profile = dict(PROFILE, preferred_activities=['running', 'cycling', 'swimming'], equipment=['bicycle'])
        pref = ProfilePreferences(profile)
        self.assertEqual(pref.usable_activities(), ['running', 'cycling'])
        self.assertEqual(pref.missing_equipment_for('swimming'), ['swimming pool'])

    def test_weekly_pattern_and_targets(self):
        planner = WeeklyPlanner(ProfilePreferences(PROFILE))
        self.assertEqual(planner.weekly_intensity_plan(1), ('moderate',))
        self.assertEqual([self.logic.weekly_target(PROFILE, n, [])['intensity'] for n in range(4)],
                         ['high', 'low', 'moderate', 'low'])
        self.assertTrue(self.logic.weekly_target(PROFILE, 4, [])['week_complete'])
        self.assertEqual(self.logic.weekly_target(PROFILE, 4, [])['intensity'], 'low')

    def test_weekly_cap(self):
        self.assertEqual(self.logic.weekly_target(dict(PROFILE, fitness_level='beginner'), 0, [])['intensity'], 'moderate')
        self.assertEqual(self.logic.weekly_target(PROFILE, 0, [past(rpe=9)])['intensity'], 'moderate')
        self.assertEqual(self.logic.weekly_target(dict(PROFILE, health_concerns='asthma'), 0, [])['intensity'], 'moderate')

    def test_weekly_rule_uses_ai_intensity_and_plan(self):
        target = self.logic.weekly_target(PROFILE, 1, [])
        self.assertEqual(self.result(target=target)['outcome'], 'reject')
        target = self.logic.weekly_target(PROFILE, 2, [])
        self.assertEqual(self.result(target=target)['outcome'], 'accept')
        self.assertEqual(self.result(session(intensity='low'), target=target)['outcome'], 'accept')

    def test_log_validator(self):
        validator = SessionLogValidator()
        self.assertEqual(validator.validate({'avg_pace_min_per_km': 6, 'avg_hr_bpm': 150}, PROFILE), [])
        self.assertEqual(len(validator.validate({'avg_pace_min_per_km': 2, 'avg_hr_bpm': 220}, PROFILE)), 2)

    def test_abstract_rule_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            FitnessRule()

    def test_inheritance_and_polymorphism(self):
        self.assertIsInstance(HeartRateRule(), FitnessRule)
        context = EvaluationContext(session(target_hr_bpm=180), PROFILE, [])
        self.assertTrue(HeartRateRule().check(context))

    def test_open_closed_new_rule_without_editing_evaluator(self):
        class NoRunningRule(HardRule):
            def check(self, context):
                return ['running disabled'] if context.session['activity'] == 'running' else []

        custom = LogicManager(rules=[NoRunningRule()])
        self.assertEqual(custom.evaluate_session(session(), PROFILE, [])['outcome'], 'reject')
        self.assertEqual(self.result()['outcome'], 'accept')

    def test_soft_rule_substitution(self):
        class CoachingNoteRule(SoftRule):
            def check(self, context):
                return ['review technique']

        evaluator = SessionEvaluator([CoachingNoteRule()])
        self.assertEqual(evaluator.evaluate(EvaluationContext(session(), PROFILE, []))['outcome'], 'flag')

    def test_rule_contract_rejects_unrelated_objects(self):
        with self.assertRaises(TypeError):
            SessionEvaluator([object()])

    def test_input_dictionaries_are_not_modified(self):
        suggested, profile, history = session(), copy.deepcopy(PROFILE), [past()]
        original = copy.deepcopy((suggested, profile, history))
        self.logic.evaluate_session(suggested, profile, history)
        self.logic.weekly_target(profile, 0, history)
        self.assertEqual((suggested, profile, history), original)




class ExistingManagerRegressionTests(unittest.TestCase):
    """Retain important Phase 1 AI/data checks while other managers await OOP refactor."""

    def test_ai_valid_json(self):
        data, error = ai_manager.parse_and_validate_response(json.dumps(GOOD_AI))
        self.assertIsNone(error)
        self.assertEqual(data['exercise_type'], 'easy run')

    def test_ai_code_fence(self):
        data, error = ai_manager.parse_and_validate_response('```json\n' + json.dumps(GOOD_AI) + '\n```')
        self.assertIsNone(error)
        self.assertEqual(data['activity'], 'running')

    def test_ai_malformed_and_missing_fields(self):
        self.assertIsNotNone(ai_manager.parse_and_validate_response('Not JSON')[1])
        for key in ('activity', 'intensity', 'equipment_used'):
            bad = session()
            bad.pop(key)
            self.assertIn(key, ai_manager.parse_and_validate_response(json.dumps(bad))[1])

    def test_ai_wrong_field_types(self):
        for bad in (session(duration_min='thirty'), session(equipment_used=[1, 2]),
                    session(intensity='insane')):
            self.assertIsNotNone(ai_manager.parse_and_validate_response(json.dumps(bad))[1])

    def test_ai_prompt_contains_weekly_target(self):
        target = LogicManager().weekly_target(PROFILE, 1, [])
        prompt = ai_manager.build_user_prompt(PROFILE, [], None, None, target)
        self.assertIn('weekly_plan_target', prompt)
        self.assertIn('dumbbells', prompt)

    def test_data_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, status = data_manager.load_data(os.path.join(tmp, 'none.json'))
        self.assertEqual(status, 'missing')
        self.assertEqual(store['sessions'], [])

    def test_data_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'data.json')
            with open(path, 'w') as handle:
                handle.write('{not valid json')
            store, status = data_manager.load_data(path)
            self.assertEqual(status, 'corrupt')
            self.assertEqual(store['sessions'], [])
            self.assertTrue(any('.corrupt-' in name for name in os.listdir(tmp)))

    def test_data_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'data.json')
            store = data_manager.empty_store()
            self.assertTrue(data_manager.save_profile(store, PROFILE, path))
            self.assertTrue(data_manager.set_pending_session(store, GOOD_AI, path))
            self.assertTrue(data_manager.complete_pending_session(store,
                            {'duration_min': 30, 'rpe': 6, 'pain_reported': True}, path))
            loaded, status = data_manager.load_data(path)
        self.assertEqual(status, 'ok')
        self.assertEqual(len(data_manager.filter_sessions(loaded, pain_only=True)), 1)

    def test_data_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'data.json')
            store = data_manager.empty_store()
            data_manager.save_profile(store, PROFILE, path)
            self.assertTrue(data_manager.reset_data(store, path))
            self.assertEqual(os.listdir(tmp), [])
        self.assertIsNone(store['profile'])

    def test_sessions_this_week(self):
        store = data_manager.empty_store()
        store['sessions'] = [
            {'id': 1, 'date': '2026-10-11', 'planned': {}, 'actual': {}},
            {'id': 2, 'date': '2026-10-12', 'planned': {}, 'actual': {}},
            {'id': 3, 'date': '2026-10-14', 'planned': {}, 'actual': {}},
        ]
        self.assertEqual(data_manager.sessions_this_week(store, today=datetime.date(2026, 10, 12)), 2)


class MainIntegrationTests(unittest.TestCase):
    def test_main_uses_oop_logic(self):
        import main
        self.assertIsInstance(main._coach.logic, LogicManager)
        target = main._coach.logic.weekly_target(PROFILE, 0, [])
        self.assertEqual(target['session_number'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
           
