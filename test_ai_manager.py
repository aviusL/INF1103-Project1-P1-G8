"""Offline tests for the AI layer. A stub LLMClient stands in for Claude, so no API key is needed."""
import json
import os
import unittest
from unittest import mock

import ai_manager
from ai_manager import (
    AIConfig, AIManager, AIResult, ChoiceField, ClaudeClient, FieldSpec, LLMClient,
    ResponseParser, ResponseSchema, SessionPromptBuilder, SessionRequest,
    StringListField, TypedField,
)
from logic_manager import LogicManager

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


def reply(**changes):
    data = dict(GOOD_AI)
    data.update(changes)
    return json.dumps(data)


class StubClient(LLMClient):
    """Returns scripted (text, error) pairs and records every prompt it receives."""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.prompts = []

    def complete(self, system_prompt, user_prompt):
        self.prompts.append(user_prompt)
        return self._replies.pop(0)


class ResponseParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = ResponseParser(ResponseSchema.for_session_plan())

    def test_valid_json(self):
        data, error = self.parser.parse(json.dumps(GOOD_AI))
        self.assertIsNone(error)
        self.assertEqual(data['exercise_type'], 'easy run')

    def test_code_fence_and_chatter_are_stripped(self):
        for text in ('```json\n' + json.dumps(GOOD_AI) + '\n```', 'Here you go: ' + json.dumps(GOOD_AI)):
            data, error = self.parser.parse(text)
            self.assertIsNone(error)
            self.assertEqual(data['activity'], 'running')

    def test_empty_and_malformed_replies(self):
        for text in ('', '   ', None, 'Not JSON', '{"exercise_type": "run"'):
            data, error = self.parser.parse(text)
            self.assertIsNone(data)
            self.assertIsNotNone(error)

    def test_reply_that_is_not_an_object(self):
        self.assertEqual(self.parser.parse('[1, 2]')[1], 'response is not a JSON object')

    def test_missing_fields(self):
        for key in ('activity', 'intensity', 'equipment_used'):
            bad = dict(GOOD_AI)
            bad.pop(key)
            self.assertIn(key, self.parser.parse(json.dumps(bad))[1])

    def test_wrong_field_types_and_values(self):
        for text in (reply(duration_min='thirty'), reply(duration_min=True), reply(equipment_used=[1, 2]),
                     reply(intensity='insane'), reply(risk_level='extreme')):
            self.assertIsNotNone(self.parser.parse(text)[1])

    def test_null_pace_is_allowed(self):
        self.assertIsNone(self.parser.parse(reply(target_pace_min_per_km=None))[1])


class ResponseSchemaTests(unittest.TestCase):
    def test_missing_field_is_reported_before_bad_value(self):
        bad = json.loads(reply(intensity='insane'))
        bad.pop('cooldown')
        self.assertEqual(ResponseSchema.for_session_plan().validate(bad), 'missing field: cooldown')

    def test_each_field_spec_applies_its_own_check(self):
        schema = ResponseSchema([TypedField('n', (int,)), ChoiceField('c', ('a', 'b')), StringListField('l')])
        self.assertIsNone(schema.validate({'n': 1, 'c': 'a', 'l': ['x']}))
        self.assertEqual(schema.validate({'n': 1, 'c': 'z', 'l': ['x']}), "c must be one of ('a', 'b')")
        self.assertEqual(schema.validate({'n': 1, 'c': 'a', 'l': [1]}), 'l must be a list of strings')

    def test_rejects_objects_that_are_not_field_specs(self):
        with self.assertRaises(TypeError):
            ResponseSchema(['not a spec'])

    def test_abstract_classes_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            FieldSpec('x', (str,))
        with self.assertRaises(TypeError):
            LLMClient()


class SessionPromptBuilderTests(unittest.TestCase):
    def test_prompt_contains_weekly_target_and_equipment(self):
        target = LogicManager().weekly_target(PROFILE, 1, [])
        prompt = SessionPromptBuilder().build(SessionRequest(PROFILE, [], weekly_target=target))
        self.assertIn('weekly_plan_target', prompt)
        self.assertIn('dumbbells', prompt)

    def test_prompt_carries_user_and_rule_feedback(self):
        prompt = SessionPromptBuilder().build(SessionRequest(PROFILE, [], 'too hard', ['heart rate too high']))
        self.assertIn('too hard', prompt)
        self.assertIn('heart rate too high', prompt)

    def test_system_prompt_asks_for_json_only(self):
        self.assertIn('Reply with ONLY one JSON object', SessionPromptBuilder().system_prompt)


class AIManagerTests(unittest.TestCase):
    def test_valid_reply_succeeds(self):
        result = AIManager(StubClient((json.dumps(GOOD_AI), None))).request_session(PROFILE, [])
        self.assertEqual(result, AIResult.success(GOOD_AI))

    def test_malformed_reply_is_retried_with_the_reason(self):
        stub = StubClient(('not json', None), (json.dumps(GOOD_AI), None))
        with self.assertLogs('ai_manager', level='WARNING'):
            result = AIManager(stub).request_session(PROFILE, [])
        self.assertTrue(result.ok)
        self.assertIn('previous reply was rejected: invalid JSON', stub.prompts[1])

    def test_gives_up_after_max_schema_attempts(self):
        stub = StubClient(('not json', None), (reply(intensity='insane'), None))
        with self.assertLogs('ai_manager', level='WARNING'):
            result = AIManager(stub, AIConfig(max_schema_attempts=2)).request_session(PROFILE, [])
        self.assertFalse(result.ok)
        self.assertTrue(result.error.startswith('AI returned malformed output: intensity'))

    def test_api_error_is_returned_without_retrying(self):
        stub = StubClient((None, 'the AI service timed out'))
        result = AIManager(stub).request_session(PROFILE, [])
        self.assertEqual(result, AIResult.failure('the AI service timed out'))
        self.assertEqual(len(stub.prompts), 1)

    def test_rejects_client_that_is_not_an_llm_client(self):
        with self.assertRaises(TypeError):
            AIManager(client=object())


class ClaudeClientTests(unittest.TestCase):
    def complete_with_sdk_error(self, error):
        failing_sdk = mock.Mock()
        failing_sdk.messages.create.side_effect = error
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}), \
                mock.patch.object(ClaudeClient, '_sdk_client', return_value=failing_sdk), \
                self.assertLogs('ai_manager', level='ERROR'):
            return ClaudeClient(AIConfig()).complete('system', 'user')

    def test_missing_api_key_returns_error_without_calling_the_api(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('ANTHROPIC_API_KEY', None)
            self.assertEqual(ClaudeClient(AIConfig()).complete('system', 'user'),
                             (None, ai_manager.NO_KEY_MESSAGE))

    @unittest.skipIf(ai_manager.anthropic is None, "anthropic package not installed")
    def test_timeout_is_reported(self):
        error = ai_manager.anthropic.APITimeoutError(request=mock.Mock())
        self.assertEqual(self.complete_with_sdk_error(error), (None, 'the AI service timed out'))

    @unittest.skipIf(ai_manager.anthropic is None, "anthropic package not installed")
    def test_connection_failure_is_reported(self):
        error = ai_manager.anthropic.APIConnectionError(request=mock.Mock())
        self.assertEqual(self.complete_with_sdk_error(error), (None, 'could not connect to the AI service'))

    @unittest.skipIf(ai_manager.anthropic is None, "anthropic package not installed")
    def test_unexpected_failure_never_raises(self):
        self.assertEqual(self.complete_with_sdk_error(RuntimeError('boom')), (None, 'unexpected AI error'))

    def test_model_can_be_set_from_environment(self):
        with mock.patch.dict(os.environ, {'CLAUDE_MODEL': 'test-model'}):
            self.assertEqual(AIConfig.from_env().model, 'test-model')


if __name__ == '__main__':
    unittest.main(verbosity=2)
