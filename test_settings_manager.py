"""Offline tests for app settings: colour maths, theme validation, API key handling,
the settings file, CoachService integration and the Claude client's key/model handling.

A fake 'anthropic' module stands in for the SDK, so no key or network is needed.
"""
import datetime
import json
import logging
import os
import stat
import tempfile
import types
import unittest
from dataclasses import replace
from unittest import mock
from zoneinfo import ZoneInfo

import ai_manager
from ai_manager import DEFAULT_MODEL, MODEL_IDS, NO_KEY_MESSAGE, AIConfig, AIManager, ClaudeClient, LLMClient
from coach_service import CoachService
from data_manager import DataConfig, DataManager, InMemoryStorage, JsonFileStorage, LoadStatus
from logic_manager import LogicManager
from settings_manager import (
    DEFAULT_THEME, THEME_PRESETS, AppSettings, PrivateJsonFileStorage, SettingsManager, ThemeColors,
    api_key_problem, clean_api_key, contrast_ratio, mask_key, mix, normalize_hex, readable_on, timezone_choices,
    valid_timezone,
)

KEY = "sk-ant-api03-" + "x" * 40 + "AbCd"


# --------------------------------------------------------------------------- #
# Colours and themes
# --------------------------------------------------------------------------- #
class ColourHelperTests(unittest.TestCase):
    def test_normalize_hex_accepts_common_spellings(self):
        self.assertEqual(normalize_hex("a3e635"), "#A3E635")
        self.assertEqual(normalize_hex(" #A3E635 "), "#A3E635")
        self.assertEqual(normalize_hex("#ae5"), "#AAEE55")

    def test_normalize_hex_rejects_non_colours(self):
        for value in ("", None, "#12345", "#GGGGGG", "lime", "#A3E6355", 42):
            self.assertIsNone(normalize_hex(value), value)

    def test_contrast_ratio_extremes(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(contrast_ratio("#777777", "#777777"), 1.0)

    def test_readable_on_picks_dark_text_for_lime(self):
        self.assertEqual(readable_on("#A3E635"), "#000000")
        self.assertEqual(readable_on("#0C0F0A"), "#FFFFFF")

    def test_mix_end_points(self):
        self.assertEqual(mix("#000000", "#FFFFFF", 0), "#000000")
        self.assertEqual(mix("#000000", "#FFFFFF", 1), "#FFFFFF")


class ThemeTests(unittest.TestCase):
    def test_default_is_dark_lime(self):
        self.assertEqual(DEFAULT_THEME.accent, "#A3E635")
        self.assertTrue(DEFAULT_THEME.is_dark)

    def test_every_preset_is_readable(self):
        for name, theme in THEME_PRESETS.items():
            self.assertEqual(theme.problems(), [], name)
            self.assertTrue(theme.is_dark, name)

    def test_faint_text_is_rejected(self):
        problems = ThemeColors(text="#333333", background="#111111", card="#181818").problems()
        self.assertTrue(any("text on background is too faint" in p for p in problems), problems)

    def test_faint_accent_is_rejected(self):
        problems = replace(DEFAULT_THEME, accent="#1A2A10").problems()
        self.assertTrue(any(p.startswith("accent on") for p in problems), problems)

    def test_invalid_hex_is_rejected(self):
        problems = replace(DEFAULT_THEME, card="#12").problems()
        self.assertEqual(problems, ["cards must be a hex colour like #A3E635"])

    def test_from_dict_keeps_valid_colours_and_defaults_the_rest(self):
        theme = ThemeColors.from_dict({"accent": "38bdf8", "card": "nonsense"})
        self.assertEqual(theme.accent, "#38BDF8")
        self.assertEqual(theme.card, DEFAULT_THEME.card)


# --------------------------------------------------------------------------- #
# API key helpers
# --------------------------------------------------------------------------- #
class ApiKeyTests(unittest.TestCase):
    def test_clean_api_key_undoes_paste_mistakes(self):
        self.assertEqual(clean_api_key("  {} \n".format(KEY)), KEY)
        self.assertEqual(clean_api_key('"{}"'.format(KEY)), KEY)
        self.assertEqual(clean_api_key('ANTHROPIC_API_KEY="{}"'.format(KEY)), KEY)

    def test_api_key_problem(self):
        self.assertIsNone(api_key_problem(""))
        self.assertIsNone(api_key_problem(KEY))
        self.assertIn("spaces", api_key_problem("sk-ant-abc def" + "x" * 30))
        self.assertIn("start with sk-ant-", api_key_problem("$env:" + KEY))
        self.assertIn("too short", api_key_problem("sk-ant-short"))

    def test_mask_key_hides_the_middle(self):
        masked = mask_key(KEY)
        self.assertEqual(masked, "sk-ant-…AbCd")
        self.assertNotIn("x" * 5, masked)
        self.assertEqual(mask_key(""), "")


# --------------------------------------------------------------------------- #
# AppSettings and the settings file
# --------------------------------------------------------------------------- #
class AppSettingsTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        self.assertEqual(AppSettings().problems(), [])

    def test_from_dict_never_raises_on_garbage(self):
        for data in (None, [], "text", {"model": 5, "theme": "lime", "show_splash": "yes", "week_starts_on": 3}):
            settings = AppSettings.from_dict(data)
            self.assertEqual(settings.problems(), [], data)

    def test_from_dict_drops_an_invalid_key_and_unknown_model(self):
        settings = AppSettings.from_dict({"api_key": "not a key", "model": "gpt-4"})
        self.assertEqual(settings.api_key, "")
        self.assertEqual(settings.model, DEFAULT_MODEL)

    def test_unreadable_stored_theme_falls_back_to_default(self):
        data = {"theme": {"accent": "#A3E635", "background": "#111111", "card": "#151515", "text": "#202020"}}
        with self.assertLogs("settings_manager", level="WARNING"):
            self.assertEqual(AppSettings.from_dict(data).theme, DEFAULT_THEME)

    def test_round_trip(self):
        settings = AppSettings(api_key=KEY, model=MODEL_IDS[0], theme=THEME_PRESETS["Ocean"],
                               week_starts_on="sunday", show_splash=False, animations=False)
        self.assertEqual(AppSettings.from_dict(json.loads(json.dumps(settings.to_dict()))), settings)


class SettingsManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, environ=None):
        return SettingsManager(PrivateJsonFileStorage(self.path), environ=environ or {})

    def test_first_run_uses_defaults(self):
        manager = self.manager()
        self.assertEqual(manager.load(), LoadStatus.MISSING)
        self.assertEqual(manager.settings, AppSettings())

    def test_save_then_load(self):
        saved = AppSettings(api_key=KEY, theme=THEME_PRESETS["Violet"], week_starts_on="sunday")
        self.assertEqual(self.manager().save(saved), [])
        manager = self.manager()
        self.assertEqual(manager.load(), LoadStatus.OK)
        self.assertEqual(manager.settings, saved)

    @unittest.skipIf(os.name == "nt", "POSIX permissions only")
    def test_file_is_private(self):
        self.manager().save(AppSettings(api_key=KEY))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_save_normalises_hex_and_key(self):
        manager = self.manager()
        theme = ThemeColors(accent="a3e635", background="0c0f0a", card="181d15", text="eef3e8")
        self.assertEqual(manager.save(AppSettings(api_key="  {}  ".format(KEY), theme=theme)), [])
        self.assertEqual(manager.settings.theme, DEFAULT_THEME)
        self.assertEqual(manager.settings.api_key, KEY)

    def test_invalid_settings_are_not_saved(self):
        manager = self.manager()
        errors = manager.save(AppSettings(theme=replace(DEFAULT_THEME, text="#101010")))
        self.assertTrue(errors)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(manager.settings, AppSettings())

    def test_disk_failure_leaves_settings_unchanged(self):
        manager = SettingsManager(InMemoryStorage(succeed=False), environ={})
        self.assertEqual(manager.save(AppSettings(api_key=KEY)), ["could not save the settings file"])
        self.assertEqual(manager.settings.api_key, "")

    def test_corrupt_file_is_backed_up_and_defaults_used(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        manager = self.manager()
        with self.assertLogs("data_manager", level="ERROR"):
            self.assertEqual(manager.load(), LoadStatus.CORRUPT)
        self.assertEqual(manager.settings, AppSettings())
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(any(name.startswith("settings.json.corrupt-") for name in os.listdir(self.tmp.name)))

    def test_saved_key_wins_over_environment(self):
        manager = self.manager(environ={"ANTHROPIC_API_KEY": "sk-ant-env"})
        self.assertEqual(manager.api_key_source(), "environment")
        manager.save(AppSettings(api_key=KEY))
        self.assertEqual(manager.api_key_source(), "settings")
        self.assertIsNone(self.manager(environ={}).api_key_source())

    def test_malformed_environment_key_is_reported(self):
        # what PowerShell passes for  -e ANTHROPIC_API_KEY=$env:sk-ant-...  ($env:sk is empty)
        manager = self.manager(environ={"ANTHROPIC_API_KEY": "-ant-api03-" + "x" * 40})
        self.assertEqual(manager.api_key_source(), "environment")
        self.assertIn("start with sk-ant-", manager.environment_key_problem())
        self.assertIsNone(self.manager(environ={"ANTHROPIC_API_KEY": KEY}).environment_key_problem())
        self.assertIsNone(self.manager(environ={}).environment_key_problem())

    def test_from_env_puts_settings_beside_the_data_file(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AEGISFIT_SETTINGS_FILE", None)
            manager = SettingsManager.from_env(DataConfig(data_file="/app/data/fitness_data.json"))
            self.assertEqual(manager.location, os.path.join("/app/data", "settings.json"))
        with mock.patch.dict(os.environ, {"AEGISFIT_SETTINGS_FILE": self.path}):
            self.assertEqual(SettingsManager.from_env().location, self.path)


# --------------------------------------------------------------------------- #
# Time zones: "today" must be the user's today, not the server's
# --------------------------------------------------------------------------- #
UTC = datetime.timezone.utc
LATE_EVENING_UTC = datetime.datetime(2026, 11, 18, 18, 30, tzinfo=UTC)    # 02:30 on 19 Nov in Singapore
EARLY_MORNING_UTC = datetime.datetime(2026, 11, 18, 2, 0, tzinfo=UTC)     # 21:00 on 17 Nov in Los Angeles


class TimezoneTests(unittest.TestCase):
    def manager(self, utcnow, timezone="", default_timezone=""):
        manager = SettingsManager(InMemoryStorage(), environ={}, default_timezone=default_timezone,
                                  utcnow=lambda: utcnow)
        if timezone:
            self.assertEqual(manager.save(AppSettings(timezone=timezone)), [])
        return manager

    def test_valid_timezone(self):
        for name in ("", "UTC", "Asia/Singapore", "America/Los_Angeles"):
            self.assertTrue(valid_timezone(name), name)
        for name in ("Mars/Olympus", "singapore", "../etc/passwd", "Asia/"):
            self.assertFalse(valid_timezone(name), name)

    def test_today_is_the_users_day_east_of_greenwich(self):
        # 18:30 UTC is already the next morning in Singapore: a UTC server would say the 18th
        self.assertEqual(self.manager(LATE_EVENING_UTC, "Asia/Singapore").today(), datetime.date(2026, 11, 19))
        self.assertEqual(self.manager(LATE_EVENING_UTC, "UTC").today(), datetime.date(2026, 11, 18))

    def test_today_is_the_users_day_west_of_greenwich(self):
        self.assertEqual(self.manager(EARLY_MORNING_UTC, "America/Los_Angeles").today(), datetime.date(2026, 11, 17))

    def test_environment_default_applies_until_settings_choose_a_zone(self):
        manager = self.manager(LATE_EVENING_UTC, default_timezone="Asia/Singapore")
        self.assertEqual(manager.timezone_name, "Asia/Singapore")
        self.assertEqual(manager.today(), datetime.date(2026, 11, 19))
        manager.save(AppSettings(timezone="UTC"))
        self.assertEqual(manager.today(), datetime.date(2026, 11, 18))

    def test_invalid_environment_zone_is_ignored(self):
        self.assertEqual(self.manager(LATE_EVENING_UTC, default_timezone="Nowhere/Land").timezone_name, "")

    def test_unknown_zone_is_not_saved(self):
        manager = self.manager(LATE_EVENING_UTC)
        errors = manager.save(AppSettings(timezone="Mars/Olympus"))
        self.assertEqual(errors, ["unknown time zone: choose one from the list, like Asia/Singapore"])
        self.assertEqual(manager.settings.timezone, "")

    def test_garbage_zone_in_a_stored_file_falls_back(self):
        self.assertEqual(AppSettings.from_dict({"timezone": "Mars/Olympus"}).timezone, "")
        self.assertEqual(AppSettings.from_dict({"timezone": 7}).timezone, "")

    def test_choices_include_common_zones(self):
        choices = timezone_choices()
        for name in ("Asia/Singapore", "Europe/London", "America/New_York", "UTC"):
            self.assertIn(name, choices)
        self.assertTrue(all(valid_timezone(name) for name in choices))

    def test_whole_app_uses_the_users_day(self):
        """A session logged at 02:30 Singapore time on the 19th is dated the 19th, even though the
        server (UTC) is still on the 18th, and 'this week' agrees."""
        manager = self.manager(LATE_EVENING_UTC, "Asia/Singapore")
        data = DataManager(InMemoryStorage({"profile": SERVICE_PROFILE, "pending_session": None, "sessions": []}),
                           clock=manager.today)
        service = CoachService(data, AIManager(RecordingClient()), LogicManager(), clock=manager.today,
                               settings=manager)
        service.load()
        self.assertEqual(service.today, datetime.date(2026, 11, 19))
        self.assertEqual(service.timezone_name, "Asia/Singapore")
        data.set_pending_session(dict(SERVICE_PLAN))
        self.assertEqual(service.log_session(SERVICE_LOG), [])
        self.assertEqual(service.history()[0]["date"], "2026-11-19")
        self.assertEqual(service.accept_session(SERVICE_PLAN), True)
        self.assertEqual(service.pending_date(), datetime.date(2026, 11, 19))

    def test_scheduled_day_is_stored_exactly_as_chosen(self):
        """accept_session / reschedule_pending keep the plain date they are given."""
        manager = self.manager(LATE_EVENING_UTC, "Asia/Singapore")
        data = DataManager(InMemoryStorage({"profile": SERVICE_PROFILE, "pending_session": None, "sessions": []}),
                           clock=manager.today)
        service = CoachService(data, AIManager(RecordingClient()), LogicManager(), clock=manager.today,
                               settings=manager)
        service.load()
        service.accept_session(SERVICE_PLAN, datetime.date(2026, 11, 20))
        self.assertEqual(service.pending_date(), datetime.date(2026, 11, 20))
        service.reschedule_pending(datetime.date(2026, 11, 25))
        self.assertEqual(service.pending_date(), datetime.date(2026, 11, 25))
        self.assertIn(datetime.date(2026, 11, 25), service.calendar_events(2026, 11))

    def test_create_uses_one_clock_for_service_and_data(self):
        root = logging.getLogger()
        handlers_before = list(root.handlers)
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {
                "FITNESS_DATA_FILE": os.path.join(folder, "d.json"), "FITNESS_LOG_FILE": os.path.join(folder, "a.log"),
                "AEGISFIT_SETTINGS_FILE": os.path.join(folder, "s.json"), "AEGISFIT_TIMEZONE": "Pacific/Auckland"}):
            try:
                service = CoachService.create()
            finally:
                # create() opens a.log for logging. Windows cannot delete an open file, so close
                # it before the temporary folder is removed (and leave logging as it was).
                for handler in root.handlers[:]:
                    if handler not in handlers_before:
                        root.removeHandler(handler)
                        handler.close()
        self.assertEqual(service.timezone_name, "Pacific/Auckland")
        self.assertEqual(service.today, datetime.datetime.now(ZoneInfo("Pacific/Auckland")).date())


SERVICE_PROFILE = {
    "age": 30, "sex": "male", "height_cm": 175, "weight_kg": 70, "fitness_level": "intermediate",
    "health_concerns": "none", "fitness_goal": "run 5k", "days_per_week": 4, "session_duration_min": 90,
    "equipment": [], "preferred_activities": ["running"],
}
SERVICE_PLAN = {
    "exercise_type": "easy run", "activity": "running", "duration_min": 30, "intensity": "moderate",
    "target_hr_bpm": 140, "target_pace_min_per_km": 6.5, "equipment_used": ["bodyweight"],
    "warmup": "5 min walk", "main_set": "20 min steady", "cooldown": "5 min walk", "rationale": "Base.",
    "risk_level": "low",
}
SERVICE_LOG = {"duration_min": 32, "avg_pace_min_per_km": 6.4, "avg_hr_bpm": 142, "rpe": 6,
               "pain_reported": False, "comments": ""}


# --------------------------------------------------------------------------- #
# CoachService integration
# --------------------------------------------------------------------------- #
class RecordingClient(LLMClient):
    def __init__(self):
        self.configured = []
        self.verified = []

    def complete(self, system_prompt, user_prompt):
        return None, "not used"

    def configure(self, api_key=None, model=None):
        self.configured.append((api_key, model))

    def verify(self, api_key=None, model=None):
        self.verified.append((api_key, model))
        return True, "ok"


class ServiceSettingsTests(unittest.TestCase):
    def make(self, settings=None):
        client = RecordingClient()
        service = CoachService(DataManager(InMemoryStorage()), AIManager(client), LogicManager(), settings=settings)
        return service, client

    def test_saved_settings_reach_the_ai_client(self):
        service, client = self.make(SettingsManager(InMemoryStorage(), environ={}))
        self.assertEqual(client.configured[-1], (None, DEFAULT_MODEL))
        self.assertEqual(service.save_settings(replace(service.settings, api_key=KEY, model=MODEL_IDS[0])), [])
        self.assertEqual(client.configured[-1], (KEY, MODEL_IDS[0]))
        self.assertEqual(service.ai_model, MODEL_IDS[0])

    def test_rejected_settings_do_not_reconfigure(self):
        service, client = self.make()
        calls = len(client.configured)
        self.assertTrue(service.save_settings(replace(service.settings, api_key="oops")))
        self.assertEqual(len(client.configured), calls)

    def test_connection_test_checks_key_format_first(self):
        service, client = self.make()
        ok, message = service.test_ai_connection("not-a-key")
        self.assertFalse(ok)
        self.assertIn("sk-ant-", message)
        self.assertEqual(client.verified, [])

    def test_connection_test_uses_typed_key_and_model(self):
        service, client = self.make()
        self.assertEqual(service.test_ai_connection(" {} ".format(KEY), MODEL_IDS[2]), (True, "ok"))
        self.assertEqual(client.verified, [(KEY, MODEL_IDS[2])])

    def test_reset_keeps_settings(self):
        settings = SettingsManager(InMemoryStorage(), environ={})
        service, _ = self.make(settings)
        service.save_settings(replace(service.settings, api_key=KEY))
        self.assertTrue(service.reset())
        self.assertEqual(service.settings.api_key, KEY)

    def test_service_without_settings_manager_uses_defaults(self):
        service, _ = self.make()
        self.assertEqual(service.settings, AppSettings())
        self.assertEqual(service.settings_location, "not saved to disk")


# --------------------------------------------------------------------------- #
# ClaudeClient with a fake SDK
# --------------------------------------------------------------------------- #
def fake_sdk(models_error=None, has_models=True, message_error=None):
    """A stand-in for the anthropic module that records every client it creates.
    models_error / message_error: functions taking the fake module and returning the exception to raise."""
    class APIConnectionError(Exception):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(Exception):
        def __init__(self, status_code):
            super().__init__("HTTP {}".format(status_code))
            self.status_code = status_code

    created = []
    sdk = types.SimpleNamespace(APIConnectionError=APIConnectionError, APITimeoutError=APITimeoutError,
                                APIStatusError=APIStatusError, created=created)

    def make_client(**kwargs):
        client = mock.Mock()
        client.kwargs = kwargs
        if has_models:
            client.models.retrieve.side_effect = models_error(sdk) if models_error else None
        else:
            del client.models
        if message_error:
            client.messages.create.side_effect = message_error(sdk)
        else:
            client.messages.create.return_value = types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="{}")])
        created.append(client)
        return client

    sdk.Anthropic = make_client
    return sdk


class ClaudeClientSettingsTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_configured_key_and_model_are_used(self):
        sdk = fake_sdk()
        with mock.patch.object(ai_manager, "anthropic", sdk):
            client = ClaudeClient(AIConfig())
            client.configure(api_key=KEY, model=MODEL_IDS[0])
            self.assertEqual(client.complete("s", "u"), ("{}", None))
        self.assertEqual(sdk.created[-1].kwargs["api_key"], KEY)
        self.assertEqual(sdk.created[-1].messages.create.call_args.kwargs["model"], MODEL_IDS[0])

    def test_new_key_builds_a_new_sdk_client(self):
        sdk = fake_sdk()
        with mock.patch.object(ai_manager, "anthropic", sdk):
            client = ClaudeClient(AIConfig(), api_key=KEY)
            client.complete("s", "u")
            client.complete("s", "u")
            client.configure(api_key=KEY + "2")
            client.complete("s", "u")
        self.assertEqual([c.kwargs["api_key"] for c in sdk.created], [KEY, KEY + "2"])

    def test_environment_key_is_the_fallback(self):
        sdk = fake_sdk()
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
        with mock.patch.object(ai_manager, "anthropic", sdk):
            ClaudeClient(AIConfig()).complete("s", "u")
        self.assertEqual(sdk.created[-1].kwargs["api_key"], "sk-ant-from-env")

    def test_rejected_key_gets_a_helpful_message(self):
        sdk = fake_sdk(message_error=lambda m: m.APIStatusError(401))
        with mock.patch.object(ai_manager, "anthropic", sdk), self.assertLogs("ai_manager", level="ERROR"):
            text, error = ClaudeClient(AIConfig(), api_key=KEY).complete("s", "u")
        self.assertIsNone(text)
        self.assertIn("rejected the API key", error)

    def test_verify_without_key(self):
        self.assertEqual(ClaudeClient(AIConfig()).verify(), (False, NO_KEY_MESSAGE))

    def test_verify_success_looks_up_the_model(self):
        sdk = fake_sdk()
        with mock.patch.object(ai_manager, "anthropic", sdk):
            ok, message = ClaudeClient(AIConfig()).verify(api_key=KEY, model=MODEL_IDS[1])
        self.assertTrue(ok, message)
        sdk.created[-1].models.retrieve.assert_called_once_with(MODEL_IDS[1])
        sdk.created[-1].messages.create.assert_not_called()

    def test_verify_falls_back_to_a_tiny_message_on_old_sdks(self):
        sdk = fake_sdk(has_models=False)
        with mock.patch.object(ai_manager, "anthropic", sdk):
            ok, _ = ClaudeClient(AIConfig(), api_key=KEY).verify()
        self.assertTrue(ok)
        self.assertEqual(sdk.created[-1].messages.create.call_args.kwargs["max_tokens"], 1)

    def test_verify_reports_bad_key_and_offline(self):
        for status, expected in ((401, "rejected the API key"), (404, "not available")):
            sdk = fake_sdk(models_error=lambda m, status=status: m.APIStatusError(status))
            with mock.patch.object(ai_manager, "anthropic", sdk), self.assertLogs("ai_manager", level="ERROR"):
                ok, message = ClaudeClient(AIConfig(), api_key=KEY).verify()
            self.assertFalse(ok)
            self.assertIn(expected, message)
        sdk = fake_sdk(models_error=lambda m: m.APIConnectionError())
        with mock.patch.object(ai_manager, "anthropic", sdk), self.assertLogs("ai_manager", level="ERROR"):
            ok, message = ClaudeClient(AIConfig(), api_key=KEY).verify()
        self.assertFalse(ok)
        self.assertIn("could not connect", message)

    def test_verify_never_raises(self):
        sdk = fake_sdk(models_error=lambda m: RuntimeError("boom"))
        with mock.patch.object(ai_manager, "anthropic", sdk), self.assertLogs("ai_manager", level="ERROR"):
            self.assertEqual(ClaudeClient(AIConfig(), api_key=KEY).verify(),
                             (False, "unexpected error while checking the connection"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
